# PROOF record — DRE-5843: PROOF: a Claude-limit death comes back by itself on the live board — the assumed clock, the watchdog's wait, the third-death hand-off and the review's re-ask, recorded in docs/claude-limit-recovery-proof-2026-10.md (epic DRE-3624)

**Status: PARTIAL.**

Read four and a half hours into a fourteen-day window. The merged marker writer works on the real failed log of run 37056534934: it printed `kind=claude stage=plan` with an assumed five-hour reset. Since the last build card merged, the board has carried no natural Claude-limit death at all, so nothing has yet had to come back. The two fixture cards that sections 3 and 4 stage were never created, and this run may not create them. No row is `Not met.` — the rest is unobserved, not failed.

## How this was recorded

- Run: proof run 37548744171 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37548744171
- Observed at: 2026-10-06 16:51 PT to 2026-10-06 16:53 PT.
- Commit observed: `main` at `fdb264a89db977300c7460024c35fa1ed241a9bc` (merge of #770). The replay in section 2 ran this checkout's `scripts/dead_run.py`.
- Release observed: the `stable` tag at `1c4ea9143a785f5881a305ed733c2dcfd19c4156` (`gh api repos/dreadnought-foundry/bureau-pipeline/git/ref/tags/stable`). `git merge-base --is-ancestor deec1ba9 1c4ea914` is true, so `stable` holds all four build merges.
- Observation window: from the last of the four merges, 2026-10-06 12:25 PT, to 2026-10-20 12:25 PT. This run read its first 4 hours 28 minutes.
- Every time below is Pacific.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** I used it for every GitHub read: the four pull requests' merge times and commits, the `stable` tag, run 37056534934's failed log and job steps, the list of Reconcile runs since the last merge, and sweep run 37548565465's log. The one write I made with it on purpose:

      GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs \
        -f ref=refs/heads/proof-write-probe-37548744171 -f sha=fdb264a89db977300c7460024c35fa1ed241a9bc

  GitHub's answer: `{"message":"Resource not accessible by integration",...,"status":"403"}` — `gh: Resource not accessible by integration (HTTP 403)`. No ref was created.
- **GitHub, written: `GH_TOKEN`.** I used it only to push `agent/DRE-5843-proof-record` and open this record's pull request.
- **Linear: `LINEAR_API_KEY`.** I used it to read: comments containing `limit-death` and `limit-recovery` since the merges, issues titled `PROOF FIXTURE`, and this card's parent. Its only writes were this card's heartbeats and the closing actor marker. I created no card and commented on no other card.
- **aws: none.** No role was provided, and no criterion here needs one.
- **Scratch state: none created.** The card names fixture cards as scratch for sections 3 and 4. It assigns their creation to the operator, by hand, and this run may not create cards or comment on any card but its own.

## Criteria

| Criterion | Result |
| -- | -- |
| `docs/claude-limit-recovery-proof-2026-10.md` is on `main` with the six sections above, a Status line at the top, times in PT, and the numbers traceable to a run id, a card comment or a command's output quoted in the document. | Not observed. The file exists on this branch with the six sections, a Status line at the top and every time in PT, and every number in it quotes a run id, a comment or a command's output. It reaches `main` only when this pull request merges, after this run ends. Sections 3 to 6 hold no observations yet — see the rows below. |
| Section 2 shows the marker the merged writer printed over run 37056534934's real failed log, with its assumed or stated reset and stage, and the first three natural Claude-limit markers observed live on the board after the merge. | Not observed. First half seen: over the real failed log, `scripts/dead_run.py decide` at `fdb264a8` printed `🪦 limit-death: kind=claude stage=plan reset=2026-10-03T01:14:40Z run=37056534934 assumed=yes`. That is five hours after the run's `updatedAt` of 2026-10-02 13:14 PT, so the reset is 2026-10-02 18:14 PT, assumed. Second half not seen: a Linear search for comments containing `limit-death: kind=claude` created after 2026-10-05 19:12 PT (DRE-5455's merge) returned 0. The newest on the board is DRE-5852's, at 2026-10-05 07:57 PT, before the merge. |
| Section 3 shows, observed live on fixture A, the watchdog's `is waiting on a claude limit death` line from a sweep run past `PLANNING_MINUTES` and before the reset, no stall-park note, and the recovery's `window reset at …` re-entry after the reset, with run ids and PT times. | Not observed. Fixture A does not exist. A Linear search for issues titled `PROOF FIXTURE`, archived included, returned seven cards, all `PROOF FIXTURE (DRE-4654): …` from 2026-09-26 and none for DRE-3624. The card gives the operator the job of creating and staging it by hand, and this run may not create cards or comment on another card. |
| Section 4 shows, observed live on fixture B, exactly one `⚠️ limit-recovery:` hand-off naming three deaths in a day, nothing moved, and the next sweep silent. | Not observed. Fixture B does not exist (same search as above). It is staged by the operator, not by this run. |
| Section 5 records a natural review death's marker, the recovery's hand-over line, the watcher's waiting reading and its re-ask after the reset with no re-plan, or says plainly that none happened in the window and marks the record Partial. | Not observed. Through 2026-10-06 16:53 PT no `stage=review` Claude marker has landed — no `kind=claude` marker of any stage has landed since the merge (row 2's search). The window stays open until 2026-10-20 12:25 PT. Whether none happens in the window can only be said once it closes. |
| Section 6 tabulates the natural Claude-limit deaths in the window, observed live, against all three KPI baselines. | Not observed. There have been zero natural Claude-limit deaths in the first 4 hours 28 minutes of the window (row 2's search), so the table has no rows and none of the three KPIs has a denominator yet. The sweep is running the merged recovery: scheduled Reconcile run 37548565465 (`reconcile.yml@refs/heads/main (598f60e8…)`, which contains all four merges) logged `limit-recovery: nothing to re-enter — 43 card(s) read, none with a limit death to recover` at 16:49 PT. |
| The fixture cards named in the record (A and B) are Canceled by the time the record merges, and the record names both with their cost in model calls. | Not observed. Neither fixture was created, so there is nothing to name, cost or cancel. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## 1. Which code ran

The four build cards merged to `main` as follows (`gh pr view <n> --json mergedAt,mergeCommit`):

| Card | Pull request | Merged (PT) | Merge commit |
| -- | -- | -- | -- |
| DRE-5455 | #745 | 2026-10-05 19:12 PT | `325727e080ceeb863db97884fd6c2576ccd61da5` |
| DRE-5640 | #757 | 2026-10-06 08:38 PT | `c1907e5adf80159c55592c52bd7cd9fdae04a9ca` |
| DRE-5841 | #758 | 2026-10-06 09:15 PT | `077e7e54778990b0b2dd57a1460736fa9dc765b2` |
| DRE-5842 | #765 | 2026-10-06 12:25 PT | `deec1ba9749714e3a2d349f084ef16ad1b450658` |

- The `main` sha that holds all four is `deec1ba9…`. `main` was at `fdb264a8…` when observed.
- The `stable` tag was at `1c4ea9143a785f5881a305ed733c2dcfd19c4156` when observed, and it contains `deec1ba9`.
- `Uses:` lines of the runs read:
  - Run 37056534934 (the death replayed in section 2) printed `Uses: dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@refs/heads/main (8fe8dfb2894301cded8c6a40815ca447422e370e)`. That predates every merge, which is why its marker is replayed by today's writer rather than read off the card.
  - Sweep run 37548565465 printed `Uses: dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@refs/heads/main (598f60e8fb3022c958d6f9ac300934c0ec80209d)`. `git merge-base --is-ancestor deec1ba9 598f60e8` is true.

## 2. The marker, over a real death

Commands, as run on `fdb264a8`:

    gh run view 37056534934 --repo dreadnought-foundry/bureau-pipeline --log-failed > /tmp/run37056534934.log    # 2,768 lines
    gh run view 37056534934 --repo dreadnought-foundry/bureau-pipeline --json updatedAt,jobs,workflowName

These came back as follows:
- `workflowName` `Agent Plan`
- `updatedAt` `2026-10-02T20:14:40Z` (2026-10-02 13:14 PT)
- job `call / bureau-card: DRE-3624`, conclusion `failure`
- failed step `Re-check the revised plan — review mode`

The log names no Claude reset time. Its only `resets` lines are the Linear budget's own (`window resets 13:49 PT; … budget: planner-oauth`).

    python3 scripts/dead_run.py decide 0 --limit-log /tmp/run37056534934.log \
      --workflow "Agent Plan (reusable)" --run-id 37056534934 \
      --now 2026-10-02T20:14:40Z --failed-step "Re-check the revised plan — review mode"

Printed, exit 0:

    limit

    🪦 limit-death: kind=claude stage=plan reset=2026-10-03T01:14:40Z run=37056534934 assumed=yes

    This run hit the Claude account's usage limit during the plan stage and stopped there. That is a wait, not a fault in this card, the model or the service: no strike is spent against this card's budget, no model is recorded as having failed, and the card is neither requeued into the same wall nor parked for a human. The run named no reset time, so the reset is assumed five hours after the run ended (one usage window), at 2026-10-02 18:14 PT, and the reconcile sweep re-enters the plan stage then. If the wall is still up then, the run dies at its first model call and is marked again, and a third death on an assumed clock within a day is handed to a person.

What the marker shows:
- `kind=claude`.
- `reset=` five hours after `--now`, with ` assumed=yes` ending the first line.
- Stage `plan`. The failed step is the re-plan's re-check in review mode, not the second critic's review: `limit_stage` maps a plan-workflow step to `review` only when its name opens with `second critic` or `review —`.

Nothing was posted.

**Natural markers after the merge: none yet.** I searched Linear comments for `limit-death: kind=claude` created after 2026-10-06T02:12:28Z (DRE-5455's merge, 2026-10-05 19:12 PT) and got 0 results.

A wider search for `limit-death` since 2026-10-04 found four Claude markers, all before the merge. Each names `reset=unknown` and none carries `assumed=yes`:

| Card | Time (PT) | First line |
| -- | -- | -- |
| DRE-5773 | 2026-10-04 10:31 PT | `🪦 limit-death: kind=claude stage=plan reset=unknown run=37220492522` |
| DRE-5771 | 2026-10-04 10:31 PT | `🪦 limit-death: kind=claude stage=plan reset=unknown run=37220487230` |
| DRE-5775 | 2026-10-04 10:32 PT | `🪦 limit-death: kind=claude stage=plan reset=unknown run=37220533605` |
| DRE-5852 | 2026-10-05 07:57 PT | `🪦 limit-death: kind=claude stage=plan reset=unknown run=37328397948` |

The same search found one marker after DRE-5455's merge, and it is a Linear one: DRE-5959 at 2026-10-05 22:57 PT, `🪦 limit-death: kind=linear stage=classify reset=unknown run=37420975002`. It is outside this proof.

## 3. A Planning card waits out its clock and comes back

Not observed. Fixture A — `PROOF FIXTURE (DRE-3624): …`, created by hand in Planning, with section 2's marker posted on it and its reset four hours ahead — does not exist. A Linear search for issues titled `PROOF FIXTURE`, archived included, returned only DRE-4982 to DRE-4988, all `PROOF FIXTURE (DRE-4654)` cards from 2026-09-26. The card makes this staging the operator's. This run may not create a card or comment on one other than its own.

## 4. The third assumed death in a day goes to a person

Not observed. Fixture B does not exist (same search). Its three `assumed=yes` markers are the operator's to post. Zero model calls were spent, because nothing was staged.

## 5. A review that died on the limit

Not observed so far. No `stage=review` Claude marker — and no Claude marker of any stage — has landed since the merge (section 2's search, through 2026-10-06 16:53 PT). The window runs to 2026-10-20 12:25 PT. Whether a natural review death happens in it can be said only when it closes. Until then, this section and the Status line stay Partial.

## 6. The natural Claude-limit deaths in the window

No rows. There were zero natural Claude-limit deaths from 2026-10-06 12:25 PT to 16:53 PT (section 2's search).

The sweep is reading for them on the merged code. Scheduled Reconcile run 37548565465, created 2026-10-06 16:48 PT on `598f60e8`, logged at 16:49 PT:

    limit-recovery: nothing to re-enter — 43 card(s) read, none with a limit death to recover

The newest recovery receipt on the board is DRE-5959's Linear-quota re-entry at 2026-10-05 23:08 PT (`🔁 limit-recovery: re-entered classify — Linear's quota answered this sweep's own board read…`). It is a Linear death, not a Claude one, and it came before the last merge.

| KPI | Baseline | In the window so far |
| -- | -- | -- |
| Share of Claude-limit deaths that came back by themselves | 0 of 66 since 2026-09-20 | no deaths yet (0 of 0) |
| Hand-offs to a person | 46 in the fourteen days to 2026-10-04 | 0 `⚠️ limit-recovery:` hand-offs (`limit-recovery` comment search since DRE-5455's merge returned DRE-5959's Linear receipt and nothing else) |
| Share of markers whose reset was assumed rather than stated | 65 of 66 named no reset | no markers yet (0 of 0) |

## What would finish this record

- **Fixtures A and B:** the operator creates them as the card describes and posts their markers. A later proof run can then read the sweep logs and comments those produce.
- **Natural deaths:** run again once natural Claude-limit deaths have landed, and again after 2026-10-20 12:25 PT when the window closes. That fills sections 2, 5 and 6.
