# PROOF record — DRE-6364: on the live board nothing automatic applies `hand-built` — a promoted PROOF card carries `no-code` alone, an OPERATOR card carries `operator-step`, the six phrase-routed cards classify FLEET, and the migration's before and after are recorded (epic DRE-6219)

**Status: FAIL.**

The board looks the way the epic says it should. No open card carries `hand-built` (68 before the migration, 0 after, read twice). The one PROOF card promoted since the release, this one, reached Hand-work wearing `no-code` and `agent:ops` and nothing else, and its receipt names only `no-code`. All twelve classifier runs on the six phrase-routed cards came back as a judgement call. None of them came back WORKBENCH.

Two rows are not met:

- **The promoting run did not name the card in its proof-dispatch step.** The run that promoted it was a scoped card-done sweep, and it skipped that step. The next scheduled run dispatched the proof 11 minutes later.
- **One migrated proof, DRE-3722, wears no `no-code` at all.** The migration took off its `hand-built` and nothing else, and it never carried `no-code`.

Three rows are not observed yet:

- No OPERATOR card has been promoted since the release.
- The two restamped code cards are still waiting under the WIP cap, so neither has a promotion receipt into Todo.
- The 48-hour window closes on 2026-10-11 at 05:07 PT.

## How this was recorded

- **Proof run:** [37969165906](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37969165906), the `Proof Task` workflow on bureau-pipeline. The sweep dispatched it at 10:51 PT on 2026-10-09 ("first proof run").
- **Commit observed:** `d7d19c5dab335df438ee47a3f4df89bbaad2342f`, the merge of #840 (DRE-6362, at 05:06:56 PT). The other last build card, #841 (DRE-6361), merged at 05:05:13 PT. The run's own checkout is the same commit.
- **Release, read off the logs:** bureau-pipeline rides `@main`. The first Reconcile run whose checkout carries `d7d19c5d` is [37927971847](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37927971847). Its `git log -1 --format=%H` printed `d7d19c5dab335df438ee47a3f4df89bbaad2342f` at 05:07:23 PT on 2026-10-09. The `stable` tag also points at `d7d19c5d` (`git/ref/tags/stable`, a compare that reads `identical`). So agent-bureau, portico and agent-bureau-demo run the same code: agent-bureau run 37968952436's log reads `reconcile.yml@refs/tags/stable (d7d19c5dab335df438ee47a3f4df89bbaad2342f)`.
- **Release time used for every window below:** 2026-10-09 05:07 PT.
- **Observed between** 10:52 PT and 11:00 PT on 2026-10-09. Every time in this record is Pacific.
- **Logs read:** every Reconcile run since the release. That is 29 runs on bureau-pipeline (to 37969452433), 35 on agent-bureau (to 37969718690), 25 on portico and 16 on agent-bureau-demo. Each was downloaded with `gh run view <id> --log` and searched for `→ Hand-work`, `→ Todo`, `card(s) promoted` and `proof-dispatch:`.

## Identities

- **github read:** `GH_READ_TOKEN`, from the step summary: `proof identity: github read — app agent-bureau-bot-2, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. It made every GitHub observation in this record: the pull requests' merge times, the run lists and logs, the jobs' step conclusions, and the `stable` tag and compare.
  - **The refused write:** `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-37969165906 -f sha=d7d19c5dab335df438ee47a3f4df89bbaad2342f`. GitHub answered `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}`. No ref was created.
- **github write:** `GH_TOKEN`, the worker token. It was used only to push `agent/DRE-6364-proof-record` and to open this record's pull request.
- **linear:** `LINEAR_API_KEY`, the fleet key (Agent-Bureau).
  - **Writes:** the heartbeats and the closing `agent-actor` marker on DRE-6364, and nothing else.
  - **Reads:** DRE-6364's and DRE-6231's labels, history and comments. The labels and lanes of the 68 migrated cards, in one request. `scripts/hand_work_migration.py census`, which is read-only. Every DRE card updated since the release, with its label history. DRE-3722's and DRE-6388's histories.
  - **linear requests: 21 of 40**, heartbeats and marker included.
- **aws:** none. The step summary reads `proof identity: aws — none, PROOF_ROLE_ARN not provided`. No criterion on this card needs it.
- **Scratch:** none was created.
- **Not reached:** atlas and deltasolv sit in other organizations, so their Reconcile logs were not read. A promotion made by their sweeps is not covered by rows 1 and 2.

## Criteria

| Criterion | Result |
|---|---|
| Live fact: as the proof-reader identity, the first `PROOF:` card the sweep promoted out of Backlog after the release is read on the live board — it sits in Hand-work wearing `no-code` and `agent:ops`, no `hand-built` and no `operator-step`, its promotion receipt names only `no-code`, and the same reconcile run's proof-dispatch step log names it (`would:` or dispatched) — card id, receipt time in PT and the log line recorded. | **Not met.** The first `PROOF:` card promoted after the release is **DRE-6364**, this card. Across all four repos' Reconcile logs since 05:07 PT, it is the only `→ Hand-work` line. It was promoted by bureau-pipeline run [37967838137](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37967838137), a `repository_dispatch` sweep with `SWEEP_REASON: card-done` and `SWEEP_CARD: DRE-6231`. That run logged `DRE-6364 → Hand-work` at 10:40:06 PT. Linear's history shows `Backlog -> Hand-work` by Agent-Bureau at 10:40:05 PT. **Read on the live board at 10:52 PT:** the lane is `Hand-work` and the labels are `repo:bureau-pipeline`, `initiative:bureau`, `agent:ops` and `no-code`. There is no `hand-built` and no `operator-step` (the migration removed `hand-built` at 10:36:42 PT, actor bureau-tools). **Receipt, at 10:40:06 PT:** `🧹 Auto-promoted Backlog → Hand-work: routed **OPERATOR** — … nothing was dispatched. Marked `no-code`.` It names only `no-code`. **Same run's proof-dispatch step: not met.** In run 37967838137 the job's `Dispatch proof runs` step concluded `skipped` (jobs API), and its log has no `proof-dispatch:` line. The card was named one run later, in scheduled run [37968978443](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37968978443), at 10:51:02 PT: `proof-dispatch: DRE-6364 — dispatched: first proof run (dispatch 1 of 2)`. Its `🔬 proof-run` receipt followed at 10:51:02 PT. One more thing, as a note: the promoting run's summary line still names the counter `1 hand-built (nothing dispatched)`, although no `hand-built` label was written. |
| Live fact: the first OPERATOR card the sweep promoted after the release is read on the live board — it sits in Hand-work wearing `operator-step` and `no-code`, no `hand-built`, and its receipt names those two marks — card id and receipt recorded. | **Not observed.** The sweep has promoted no OPERATOR card since the release. The only `→ Hand-work` line in every Reconcile log since 05:07 PT is DRE-6364, a proof (row 1). Every other promotion was `→ Todo`: DRE-3242 and DRE-6056 on bureau-pipeline in run 37967375839, plus five parentless one-offs on agent-bureau. Every promotion summary on portico and agent-bureau-demo read `0 card(s) promoted`. The migration's operator card, DRE-6231, was never promoted: its history reads `Backlog -> Done` by bureau-tools at 10:39:34 PT. While it waited, every sweep logged `held for a human ('needs-human' label) — never auto-promoted`. Sixteen of the 26 operator steps now wearing `operator-step` also carry `needs-human`, which the sweep never promotes. |
| Live fact: `python3 scripts/routing_verdict.py classify` is run against the live checkout for each of the six fixture cards (DRE-6143, DRE-5952, DRE-6164, DRE-6166, DRE-5960, DRE-6043), verbatim and reconstructed criteria, and the printed verdict for every one is FLEET or a judgement call — the twelve outputs recorded. | **Met.** The classifier was run at `d7d19c5d`, 10:55 PT. Each card's title, labels and criteria come from `tests/fixtures/routing-hand-work-2026-10-07.json`, passed as `--title`, `--label` and a `## Acceptance criteria` body. The "reconstructed" run uses the fixture's `restored` criteria for DRE-6164, DRE-6166, DRE-5960 and DRE-6043. DRE-6143 and DRE-5952 were moved, not reworded, so they have no reconstruction, and their second run uses their verbatim criteria again. All twelve exited 0 and printed the same answer: `{"verdict": null, "source": "judgement", "reason": "the acceptance criteria name no rendered outcome a screenshot can check, so whether an unattended agent can satisfy them is a judgement call.", "needs_model": true, "destination": null, "actor": null, "plan_questions": []}`. That is DRE-6143 verbatim and reconstructed, DRE-5952 verbatim and reconstructed, DRE-6164 verbatim (7 lines) and reconstructed (7 lines), DRE-6166 verbatim (7 lines) and reconstructed (8 lines), DRE-5960 verbatim and reconstructed, and DRE-6043 verbatim and reconstructed. All twelve are a judgement call, and none is WORKBENCH. |
| Live fact: the migration's before and after, read off the operator card's posts and re-read on the live board as the proof-reader identity — the count of open cards carrying `hand-built` whose adding actor was a pipeline identity or the operator's account, before the apply and after (after is 0), the proofs now wearing `no-code` alone, the operator steps and epics now wearing `operator-step`, and for each code card the lane it was moved to and, for a restamped one, the sweep's promotion receipt into Todo — in a table with card ids. | **Not met.** The table is under "Migration, before and after" below. The before and after counts are met: **68 before, 0 after.** 49 of the 68 were added by pipeline identities (Agent-Bureau and bureau-tools) and 19 by the operator's account (Frederick Conklin). The operator posted that census on DRE-6231 for 10:32 PT. The after count is 0 in the operator's census at 10:39 PT and again in this run's own `hand_work_migration.py census` at 10:56 PT: `0 card(s): … kept — applied by hand 0, could not tell — left alone 0`. All 26 operator steps and epics now wear `operator-step`. **Not met:** 30 of the 31 proofs wear `no-code` with no `hand-built` and no `operator-step`, but **DRE-3722** wears only `repo:portico`, `agent:ops`, `size:S` and `initiative:foundry`. It has no `no-code`. Its history shows one label change, `- hand-built` by bureau-tools at 10:38:11 PT, so it never carried `no-code`. **Not observed:** DRE-6209 and DRE-6210 were restamped FLEET and moved from Hand-work to Backlog, and both still read `Backlog` at 10:56 PT. Neither has a promotion receipt into Todo. Agent-bureau run 37968952436 logged at 10:51:44 PT: `promotion: WIP at cap (8/8) — no card is dispatched this sweep` and `43 candidate(s) not considered this sweep, lowest-numbered DRE-2828`. DRE-6375 went to Planning, as it should, and now reads `Green Light`. |
| Live fact: over the 48 hours of sweeps following the release, the label history of every card that gained `hand-built` in that window is read as the proof-reader identity, and none of the adding actors is a pipeline identity — the window, the query and the count recorded (a count of zero cards gaining the label is a pass and is recorded as such). | **Not observed.** The window runs from 05:07 PT on 2026-10-09 to 05:07 PT on 2026-10-11, and it had run less than six hours when this was read. **Interim reading, 05:07 PT to 10:58 PT on 2026-10-09.** The query was Linear `issues(filter: {team: {key: {eq: "DRE"}}, updatedAt: {gte: "2026-10-09T12:07:17Z"}})` with each card's `labels`, `creator` and `history(first: 40) { createdAt actor { name } addedLabels { name } }`. It returned 182 cards in 4 pages. Linear writes no history row for a label set at creation, so creation labels were read off the current labels and the migration census. **Count: 2 cards gained `hand-built` in the window, and neither adding actor is a pipeline identity.** DRE-6388 was created with it by Frederick Conklin, the operator's account, at 08:57 PT, and is now `Done`. DRE-6402 was created with it by Frederick Conklin at 10:26 PT, and the migration took it off at 10:36 PT. No history entry in the window adds `hand-built`, from any actor. The pipeline identities are Agent-Bureau, bureau-tools and bureau-sandbox (`config/linear-identities.json`). Five cards' histories came back incomplete (`hasNextPage`): DRE-5377, DRE-4915, DRE-4198, DRE-3244 and DRE-3075. An addition beyond their first 40 entries would not show here. |
| The record is the one `.md` file this card's pull request adds under `docs/`, each criterion above its own judged row in the record's table, and the CEO closes this card after reading it. | **Met.** This file, `docs/hand-built-proof-2026-10.md`, is the only file the pull request adds or changes. Each of the five criteria above has its own row, and each row opens with `Met.`, `Not met.` or `Not observed.`. The CEO's close is the next row. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## Migration, before and after

**Before** is read off DRE-6231's post "Before: the census as read, 2026-10-09 10:32 PT", written by bureau-tools at 10:39 PT. **The apply** is read off its post for 10:36:32 to 10:38:15 PT: `run --apply --include-actor "Frederick Conklin" --operator-step DRE-5530 DRE-5531`, which changed 68 cards with 0 refused and 0 failed. **Now** was re-read by this run at 10:56 PT, in one Linear request for all 68 cards. `repo:`, `initiative:` and `size:` labels are left out below.

| What | Before (10:32 PT) | After (re-read 10:56 PT) |
|--|--|--|
| Open cards carrying `hand-built` added by a pipeline identity or the operator's account | 68: Agent-Bureau and bureau-tools on 49, Frederick Conklin on 19, the CEO's account on none, "could not tell" on none | **0**, by this run's census (`0 card(s)`) and the operator's at 10:39 PT |

**Proofs: `hand-built` removed, nothing added (31).** The cards are in their lane now, with their labels other than `repo:`, `initiative:` and `size:`.

| Card | Lane now | Labels now | `no-code` alone? |
|--|--|--|--|
| DRE-6381 | Backlog | agent:ops, no-code | yes |
| DRE-6364 | Hand-work | agent:ops, no-code | yes |
| DRE-6353 | Green Light | agent:ops, needs-human, no-code | yes (and the `needs-human` hold) |
| DRE-6347 | Hand-work | agent:ops, no-code | yes |
| DRE-6274 | Hand-work | agent:ops, no-code | yes |
| DRE-6241 | Hand-work | agent:ops, no-code | yes |
| DRE-6224 | Hand-work | agent:ops, no-code | yes |
| DRE-6211 | Backlog | agent:ops, no-code | yes |
| DRE-6204 | Hand-work | agent:ops, no-code | yes |
| DRE-6132 | Hand-work | agent:ops, no-code | yes |
| DRE-6123 | Backlog | agent:ops, no-code | yes |
| DRE-6113 | Backlog | agent:ops, no-code | yes |
| DRE-6087 | Backlog | agent:ops, no-code | yes |
| DRE-6082 | Done | agent:ops, needs-human, no-code | yes (and the `needs-human` hold) |
| DRE-6046 | Backlog | agent:ops, no-code | yes |
| DRE-6042 | Backlog | agent:ops, no-code | yes |
| DRE-6008 | Backlog | agent:ops, needs-human, no-code | yes (and the `needs-human` hold) |
| DRE-5978 | Backlog | agent:ops, no-code | yes |
| DRE-5830 | Hand-work | agent:ops, no-code | yes |
| DRE-5643 | In Review | agent:ops, needs-human, no-code | yes (and the `needs-human` hold) |
| DRE-5638 | Backlog | agent:ops, no-code | yes |
| DRE-5409 | Hand-work | agent:ops, no-code | yes |
| DRE-5407 | Hand-work | agent:ops, no-code | yes |
| DRE-5377 | Hand-work | agent:ops, no-code | yes |
| DRE-5357 | Backlog | agent:ops, no-code | yes |
| DRE-5186 | Backlog | agent:ops, no-code | yes |
| DRE-4667 | Backlog | agent:ops, no-code | yes |
| DRE-4249 | Backlog | agent:ops, no-code | yes |
| DRE-4205 | Backlog | agent:ops, no-code | yes |
| DRE-4193 | Backlog | agent:ops, no-code | yes |
| **DRE-3722** | Backlog | agent:ops | **no: it has no `no-code`**. It never carried one, and the migration removed only `hand-built` (10:38:11 PT) |

**Operator steps and epics: `hand-built` swapped for `operator-step` (26).** Every one wears `operator-step` now.

| Card | Lane now | Labels now |
|--|--|--|
| DRE-6231 | Done | agent:devops, no-code, operator-step |
| DRE-6161 | Backlog | agent:devops, agent:engineer, needs-human, no-code, operator-step |
| DRE-6109 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6097 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6096 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6095 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6075 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6036 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6034 | In Review | agent:devops, needs-human, no-code, operator-step |
| DRE-5999 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-5977 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-5871 | Backlog | agent:ops, needs-human, no-code, operator-step |
| DRE-5774 | Hand-work | agent:ops, no-code, operator-step |
| DRE-5531 | Backlog | agent:engineer, operator-step |
| DRE-5530 | Hand-work | agent:engineer, operator-step |
| DRE-5174 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-4863 | Backlog | agent:ops, needs-human, no-code, operator-step |
| DRE-4710 | Hand-work | agent:ops, no-code, operator-step |
| DRE-4672 | Backlog | agent:ops, needs-human, no-code, operator-step |
| DRE-4541 | Hand-work | operator-step |
| DRE-4434 | Intake | agent:ops, needs-human, no-code, operator-step |
| DRE-4267 (epic) | In Progress | agent:planner, operator-step |
| DRE-4198 (epic) | Planning | Bug, agent:devops, agent:planner, operator-step |
| DRE-3919 (epic) | Intake | agent:planner, operator-step |
| DRE-3721 | Backlog | agent:ops, needs-human, no-code, operator-step |
| DRE-3711 (epic) | Intake | agent:planner, operator-step |

**Code cards (11).**

| Card | What the migration did | Lane now | Promotion receipt into Todo |
|--|--|--|--|
| DRE-6209 | restamped FLEET, Hand-work → Backlog | Backlog | **none yet.** Agent-bureau run 37968952436 at 10:51:44 PT: `WIP at cap (8/8)`, `43 candidate(s) not considered` |
| DRE-6210 | restamped FLEET, Hand-work → Backlog | Backlog | **none yet**, same run and lines |
| DRE-6375 | Hand-work → Planning (no critic pass) | Green Light | not restamped, so none is owed |
| DRE-6402 | label removed only (In Review) | Done | not restamped |
| DRE-5656 | label removed only (Intake) | Intake | not restamped |
| DRE-5657 | label removed only (Intake) | Intake | not restamped |
| DRE-4829 | label removed only (Intake) | Intake | not restamped |
| DRE-4417 | label removed only (Intake) | Intake | not restamped |
| DRE-4415 | label removed only (Intake) | Intake | not restamped |
| DRE-4391 | label removed only (Intake) | Intake | not restamped |
| DRE-2994 | label removed only (Intake) | Intake | not restamped |

That makes 31 proofs, 26 operator steps and epics, and 11 code cards, 68 in all, which matches the apply's `changed 68 card(s)`.
