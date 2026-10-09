# PROOF record — DRE-6364: on the live board nothing automatic applies `hand-built` — a promoted PROOF card carries `no-code` alone, an OPERATOR card carries `operator-step`, the six phrase-routed cards classify FLEET, and the migration's before and after are recorded (epic DRE-6219)

**Status: FAIL.**

The headline holds: no open card carries `hand-built`. It was 68 before the migration and 0 after, and it was still 0 when this run re-read the board at 15:58 PT. The one PROOF card the sweep promoted first went to Hand-work wearing `no-code` and `agent:ops` and nothing else. All twelve classifier runs on the six phrase-routed cards came back as a judgement call, and none came back WORKBENCH.

Three rows are not met:

- **The promoting run did not name the card in its proof-dispatch step.** It was a scoped card-done sweep, and that step runs on full passes only. The next scheduled run dispatched the proof 11 minutes later. This is history, and no later run can change it.
- **Four epics no longer wear `operator-step`.** The migration put it on DRE-4267, DRE-4198, DRE-3919 and DRE-3711 at 10:38 PT. The operator-tools identity, `bureau-tools`, took it off DRE-4198 at 10:57 PT and off the other three at 12:49 PT. No comment on DRE-4267 explains why. The earlier gap on this row is closed: DRE-3722 now wears `no-code`, which the operator's account added by hand at 14:38 PT.
- **Two cards gained `hand-built` inside the 48-hour window from `bureau-tools`.** DRE-6375 had it added at 10:57 PT, on the CEO's answer. DRE-6498 was created with it at 15:29 PT. `config/linear-identities.json` declares `bureau-tools` a non-human identity, and the migration itself counted its adds as automatic. On the test as written, both are pipeline adds. The fleet's own identity, `Agent-Bureau`, added it to no card.

One row is still waiting. No OPERATOR card has been promoted since the release. The row now names that event: when it happens, the operator records it and the record is re-run.

Which gets to the open question: whether a mark the operator applies through `bureau-tools`, and whether the epics losing `operator-step`, were decisions. Both are the operator's to settle with a decision comment. This run cannot accept a row.

## How this was recorded

- **This run (third pass, re-run after the gate's decline at `a13567f`):** [38001467320](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38001467320), the `Proof Task` workflow on bureau-pipeline. It started at 15:51 PT on 2026-10-09 and observed between 15:52 PT and 16:00 PT. It resumed the branch, merged main (`bcb36adf`) into it, and amended this file. It did not start over.
- **First run:** [37969165906](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37969165906), dispatched by the sweep at 10:51 PT ("first proof run"), observed 10:52 to 11:00 PT. Its readings are kept below where nothing has changed since.
- **Second pass:** an operator-session helper amended this file by hand from 14:15 to 14:30 PT, after the gate's first decline. It used other identities, named in "Amended by hand" at the end.
- **Commit observed (release):** `d7d19c5dab335df438ee47a3f4df89bbaad2342f`, the merge of #840 (DRE-6362, at 05:06:56 PT). The other last build card, #841 (DRE-6361), merged at 05:05:13 PT.
- **Release, read off the logs:** bureau-pipeline rides `@main`. The first Reconcile run whose checkout carries `d7d19c5d` is [37927971847](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37927971847). Its `git log -1 --format=%H` printed `d7d19c5dab335df438ee47a3f4df89bbaad2342f` at 05:07:23 PT on 2026-10-09.
- **Release time used for every window below:** 2026-10-09 05:07 PT.
- **This run's checkout:** `d80f4449`, which is this branch with main at `bcb36adf` merged in. The classifier in row 3 ran there. The `stable` tag now points at `c0e68f6a`, so the code the other repos' sweeps run has moved on since the release. It still carries every build card's merge.
- **Logs this run read:** every Reconcile run from 10:55 PT to 15:53 PT on 2026-10-09. That is 56 runs on bureau-pipeline (`self-reconcile.yml`), 52 on agent-bureau, 26 on portico and 15 on agent-bureau-demo, 149 in all. Each was listed through the workflow's own runs endpoint, downloaded with `gh run view <id> --log`, and searched for `→ Hand-work`, `→ Todo`, `card(s) promoted`, `proof-dispatch:` and `operator`. Two agent-bureau runs were cancelled and kept no log: 37981087072 (12:33 PT) and 37997104322 (15:03 PT). The first run read every run from the release to 10:58 PT.
- Every time in this record is Pacific.

## Identities

- **github read:** `GH_READ_TOKEN`, from this run's step summary: `proof identity: github read — app agent-bureau-bot-2, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. It made every GitHub observation this run made: the run lists, the run logs, the `stable` tag, and pull request #844's comments.
  - **The refused write:** `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-38001467320 -f sha=bcb36adf60370d0e8052c5a3f81021900f93165b`. GitHub answered `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}`. This run made the probe three times: once with the local merge sha, once with a mistyped sha, and once with main's sha as quoted. All three were refused with the same 403. `git ls-remote --heads origin proof-write-probe-38001467320` returns nothing, so no ref was created.
  - The first run's probe, `proof-write-probe-37969165906`, was refused the same way.
- **github write:** `GH_TOKEN`, the worker token. It was used only to push `agent/DRE-6364-proof-record`. Pull request #844 was already open, so none was opened.
- **linear:** `LINEAR_API_KEY`, the fleet key (Agent-Bureau).
  - **Writes:** the five heartbeats and the closing `agent-actor` marker on DRE-6364, and nothing else.
  - **Reads:**
    - one paged query for every DRE card updated since the release, with labels, creator and label history (7 requests, 332 cards);
    - one query for every DRE card carrying `hand-built` outside a done or canceled state, archived ones included (1 request);
    - the comments of DRE-4267, DRE-6209 and DRE-6210 (1 request each).
  - **linear requests: 23 of 40.** That is 13 reads plus 10 for the writes: the five heartbeats and the actor marker are 2 requests each, and the `⏳ 1/5 read` heartbeat is counted among the 10. The total was summed from each invocation's `linear-calls:` line. The first run spent 21 of 40 on its own pass.
- **aws:** none. The step summary reads `proof identity: aws — none, PROOF_ROLE_ARN not provided`. No criterion on this card needs it.
- **Scratch:** none was created. The probe ref was refused, so there is nothing to delete.
- **Not reached:** atlas and deltasolv sit in other organizations, so their Reconcile logs were not read. A promotion made by their sweeps is not covered by rows 1 and 2.

## Criteria

| Criterion | Result |
|---|---|
| Live fact: as the proof-reader identity, the first `PROOF:` card the sweep promoted out of Backlog after the release is read on the live board — it sits in Hand-work wearing `no-code` and `agent:ops`, no `hand-built` and no `operator-step`, its promotion receipt names only `no-code`, and the same reconcile run's proof-dispatch step log names it (`would:` or dispatched) — card id, receipt time in PT and the log line recorded. | **Not met.** The first `PROOF:` card promoted after the release is **DRE-6364**, this card. Across all four repos' Reconcile logs since 05:07 PT, its `→ Hand-work` is the first one. Later ones are DRE-5196 at 11:07 PT and DRE-6042 at 11:55 PT, both proofs. It was promoted by bureau-pipeline run [37967838137](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37967838137), a `repository_dispatch` sweep with `SWEEP_REASON: card-done` and `SWEEP_CARD: DRE-6231`. That run logged `DRE-6364 → Hand-work` at 10:40:06 PT, and Linear's history shows `Backlog -> Hand-work` by Agent-Bureau at 10:40:05 PT. **Read on the live board at 10:52 PT:** it sat in `Hand-work`, with labels `repo:bureau-pipeline`, `initiative:bureau`, `agent:ops` and `no-code`, and no `hand-built` or `operator-step`. **Re-read at 15:57 PT:** it is now `In Review`, carried there by its open record pull request #844, and its labels are unchanged. Its only label change since the release is `- hand-built` by bureau-tools at 10:36:42 PT. **Receipt, at 10:40:06 PT:** `🧹 Auto-promoted Backlog → Hand-work: routed **OPERATOR** — … nothing was dispatched. Marked `no-code`.` It names only `no-code`. **The same run's proof-dispatch step: not met.** In run 37967838137 the job's `Dispatch proof runs` step concluded `skipped` (jobs API), and its log has no `proof-dispatch:` line. That step is `if: inputs.sweep_reason == ''`, full passes only (DRE-5926). The card was named one run later, in scheduled run [37968978443](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37968978443), at 10:51:02 PT: `proof-dispatch: DRE-6364 — dispatched: first proof run (dispatch 1 of 2)`. A note: the promoting run's summary line still names the counter `1 hand-built (nothing dispatched)`, although no `hand-built` label was written. |
| Live fact: the first OPERATOR card the sweep promoted after the release is read on the live board — it sits in Hand-work wearing `operator-step` and `no-code`, no `hand-built`, and its receipt names those two marks — card id and receipt recorded. | **Not observed. waiting for the sweep to promote an OPERATOR card out of Backlog after the release: that card's id, its lane `Hand-work` with `operator-step` and `no-code` and no `hand-built`, and its promotion receipt naming those two marks.** As of 15:53 PT the sweep has promoted none. The only `→ Hand-work` lines in every Reconcile log since 05:07 PT are three proofs: DRE-6364 at 10:40 PT, DRE-5196 at 11:07:19 PT and DRE-6042 at 11:55:54 PT. This run read every log from 10:55 to 15:53 PT, 149 runs. DRE-6211 reached Hand-work at 11:37 PT without a promotion line in any of them. The only Reconcile run near that time, agent-bureau 37974575763 (card-done DRE-6210), logged `0 card(s) promoted`. The migration's operator card, DRE-6231, was never promoted: it went `Backlog -> Done` by bureau-tools at 10:39:34 PT. Why none has come yet: most operator steps carry `needs-human`, which the sweep holds. DRE-6427, the change that lets the sweep carry an operator-held card to Hand-work once its blockers are done, merged at 14:34 PT (#862). Since then the sweep's only `operator` lines are the watchdog's notes on DRE-5774, DRE-5530, DRE-4710 and DRE-4541, which were already in Hand-work. |
| Live fact: `python3 scripts/routing_verdict.py classify` is run against the live checkout for each of the six fixture cards (DRE-6143, DRE-5952, DRE-6164, DRE-6166, DRE-5960, DRE-6043), verbatim and reconstructed criteria, and the printed verdict for every one is FLEET or a judgement call — the twelve outputs recorded. | **Met.** This run ran the classifier again at 15:58 PT, at `d80f4449`, which is main `bcb36adf` plus this record. The first run had run it at `d7d19c5d` at 10:55 PT, with the same twelve answers. Each card's title, labels and criteria come from `tests/fixtures/routing-hand-work-2026-10-07.json`, passed as `--title`, `--label` and a `## Acceptance criteria` body in `--body-file`. The reconstructed run uses the fixture's `restored.criteria` for DRE-6164, DRE-6166, DRE-5960 and DRE-6043. Each of those carries the quoted phrase, checked in the body passed. DRE-6143 and DRE-5952 were moved, not reworded, so they have no reconstruction, and their second run is their verbatim criteria again. All twelve exited 0 and printed the same answer: `{"verdict": null, "source": "judgement", "reason": "the acceptance criteria name no rendered outcome a screenshot can check, so whether an unattended agent can satisfy them is a judgement call.", "needs_model": true, "destination": null, "actor": null, "plan_questions": []}`. The twelve are DRE-6143 verbatim (10 lines) and again (10), DRE-5952 verbatim (3) and again (3), DRE-6164 verbatim (7) and reconstructed (7), DRE-6166 verbatim (7) and reconstructed (8), DRE-5960 verbatim (3) and reconstructed (3), and DRE-6043 verbatim (6) and reconstructed (6). All twelve are a judgement call, and none is WORKBENCH. |
| Live fact: the migration's before and after, read off the operator card's posts and re-read on the live board as the proof-reader identity — the count of open cards carrying `hand-built` whose adding actor was a pipeline identity or the operator's account, before the apply and after (after is 0), the proofs now wearing `no-code` alone, the operator steps and epics now wearing `operator-step`, and for each code card the lane it was moved to and, for a restamped one, the sweep's promotion receipt into Todo — in a table with card ids. | **Not met.** The tables are under "Migration, before and after" below, re-read at 15:57 PT. **Met: the counts.** 68 before and 0 after. 49 of the 68 were added by pipeline identities (Agent-Bureau and bureau-tools) and 19 by the operator's account (Frederick Conklin). The before count is DRE-6231's census post for 10:32 PT. The after count was 0 in the operator's census at 10:39 PT and in the first run's `hand_work_migration.py census` at 10:56 PT. At 15:58 PT this run asked Linear for every DRE card carrying `hand-built` outside a done or canceled state, archived ones included. It got 22 cards. 17 are in `Duplicate`, one of them also archived (DRE-2575). Three are archived (DRE-6140, DRE-6135 and DRE-3611), and two are trashed and archived (DRE-5313 and DRE-5312). None is on the live board. **Met: the proofs.** All 31 now wear `no-code`, with no `hand-built` and no `operator-step`. **DRE-3722** got its `no-code` by hand, from Frederick Conklin at 14:38:22 PT, after the migration had removed only `hand-built` (10:38:11 PT). **Not met: the operator steps and epics.** 22 of the 26 wear `operator-step`, but the four epics do not. The migration added it to DRE-4267 at 10:38:03, DRE-4198 at 10:38:07, DRE-3919 at 10:38:10 and DRE-3711 at 10:38:14. bureau-tools removed it from DRE-4198 at 10:57:48 PT (while adding `initiative:bureau`), and from DRE-3711, DRE-3919 and DRE-4267 at 12:49:33, 12:49:34 and 12:49:35 PT. DRE-4267's comments hold no note of the removal: its last comment is the migration's own. **Met: the code cards.** DRE-6209 and DRE-6210 were restamped FLEET and moved from Hand-work to Backlog. Scheduled agent-bureau sweep [37970787194](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/37970787194) (`sweep-scope: full pass (schedule)`) logged `DRE-6209 → Todo` and `DRE-6210 → Todo` at 11:07:42 PT, and `promotion: 2 card(s) promoted … (WIP 6+2/8)`. This run read both receipts on the cards: `🧹 Auto-promoted Backlog → Todo: parent epic active and all blockers Done.` (11:07:35 and 11:07:36 PT, per the second pass). Both are `Done` now. DRE-6375 went to Planning, and the other eight code cards had the label removed in place. |
| Live fact: over the 48 hours of sweeps following the release, the label history of every card that gained `hand-built` in that window is read as the proof-reader identity, and none of the adding actors is a pipeline identity — the window, the query and the count recorded (a count of zero cards gaining the label is a pass and is recorded as such). | **Not met.** **Window:** 05:07 PT on 2026-10-09 to 05:07 PT on 2026-10-11. This run read it from 05:07:17 PT to 15:56:55 PT on 2026-10-09. **Query:** Linear `issues(includeArchived: true, filter: {team: {key: {eq: "DRE"}}, updatedAt: {gte: "2026-10-09T12:07:17Z"}})`, with each card's `creator`, `createdAt`, `labels` and `history(first: 40) { createdAt actor { name } addedLabels { name } removedLabels { name } }`. It returned 332 cards in 7 pages. Linear lists history newest first. Nine cards had more than 40 entries, and for each the 40 returned reach back before the window, so nothing in the window was cut off. Linear writes no history row for a label set at creation, so a card created in the window wearing the label, with no history row adding it, was counted as gaining it at creation. **Count: 6 cards gained `hand-built` in the window, 2 of them from `bureau-tools`.** **DRE-6375**: added at 10:57:45 PT by `bureau-tools`. The operator posted at 10:58:55 PT that he applied it on the CEO's answer to the card's planning escalation ("Build it by hand"). **DRE-6498**: created at 15:29:03 PT by `bureau-tools`, wearing `hand-built`. It is the card behind pull request #875, now `Done`. The other four were created with it by Frederick Conklin, the operator's account: DRE-6388 (08:57:39 PT), DRE-6402 (10:26:25 PT; the migration took it off at 10:36:37 PT), DRE-6463 (11:54:13 PT) and DRE-6475 (12:54:47 PT). Neither Agent-Bureau nor bureau-sandbox added it to any card in the window. **Why the `bureau-tools` adds fail the test as written:** `config/linear-identities.json` declares bureau-tools the operator-tools user, one of the three non-human Linear users, and the migration counted its adds as automatic. Nothing in the pipeline's code applied the label: `linear_ops` refuses `hand-built` as a label write (DRE-6361). The first run's reading to 10:58 PT missed DRE-6375's add, most likely because the query ran just before it. The second pass found it. |
| The record is the one `.md` file this card's pull request adds under `docs/`, each criterion above its own judged row in the record's table, and the CEO closes this card after reading it. | **Met.** This file, `docs/hand-built-proof-2026-10.md`, is the only file the pull request adds or changes (`git diff --stat origin/main...HEAD`). Each of the five criteria above has its own row, and each row opens with `Met.`, `Not met.` or `Not observed.`. The CEO's close is the next row. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## Migration, before and after

**Before** is read off DRE-6231's post "Before: the census as read, 2026-10-09 10:32 PT", written by bureau-tools at 10:39 PT. **The apply** is read off its post for 10:36:32 to 10:38:15 PT: `run --apply --include-actor "Frederick Conklin" --operator-step DRE-5530 DRE-5531`, which changed 68 cards with 0 refused and 0 failed. **Now** was re-read by this run at 15:57 PT, all 68 cards in the paged query of row 5. `repo:`, `initiative:` and `size:` labels are left out below.

| What | Before (10:32 PT) | After (10:39 PT, 10:56 PT, re-read 15:58 PT) |
|--|--|--|
| Open cards carrying `hand-built` added by a pipeline identity or the operator's account | 68: Agent-Bureau and bureau-tools on 49, Frederick Conklin on 19, the CEO's account on none, "could not tell" on none | **0**, by the operator's census (10:39 PT), the first run's census (`0 card(s)`, 10:56 PT), and this run's query at 15:58 PT (22 still wear it, every one `Duplicate`, archived or trashed) |

**Proofs: `hand-built` removed, nothing added (31).** The cards are in their lane now, with their labels other than `repo:`, `initiative:` and `size:`.

| Card | Lane now (15:57 PT) | Labels now | `no-code` alone? |
|--|--|--|--|
| DRE-6381 | Backlog | agent:ops, no-code | yes |
| DRE-6364 | In Review | agent:ops, no-code | yes |
| DRE-6353 | Done | agent:ops, no-code | yes |
| DRE-6347 | In Review | agent:ops, no-code | yes |
| DRE-6274 | Done | agent:ops, no-code | yes |
| DRE-6241 | Hand-work | agent:ops, no-code | yes |
| DRE-6224 | Done | agent:ops, no-code | yes |
| DRE-6211 | In Review | agent:ops, no-code | yes |
| DRE-6204 | Hand-work | agent:ops, no-code | yes |
| DRE-6132 | Done | agent:ops, no-code | yes |
| DRE-6123 | Backlog | agent:ops, no-code | yes |
| DRE-6113 | Backlog | agent:ops, no-code | yes |
| DRE-6087 | Backlog | agent:ops, no-code | yes |
| DRE-6082 | Done | agent:ops, no-code | yes |
| DRE-6046 | Backlog | agent:ops, no-code | yes |
| DRE-6042 | In Review | agent:ops, no-code | yes |
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
| DRE-4249 | Canceled | agent:ops, no-code | yes |
| DRE-4205 | Backlog | agent:ops, no-code | yes |
| DRE-4193 | Backlog | agent:ops, no-code | yes |
| DRE-3722 | Backlog | agent:ops, no-code | yes, since 14:38:22 PT, added by hand by Frederick Conklin. The migration removed only `hand-built` (10:38:11 PT) |

**Operator steps and epics: `hand-built` swapped for `operator-step` (26).** 22 wear `operator-step` now. The four epics do not.

| Card | Lane now (15:57 PT) | Labels now |
|--|--|--|
| DRE-6231 | Done | agent:devops, no-code, operator-step |
| DRE-6161 | Done | agent:devops, agent:engineer, no-code, operator-step |
| DRE-6109 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6097 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6096 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6095 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6075 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6036 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-6034 | Done | agent:devops, no-code, operator-step |
| DRE-5999 | Done | agent:devops, no-code, operator-step |
| DRE-5977 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-5871 | Backlog | agent:ops, needs-human, no-code, operator-step |
| DRE-5774 | Hand-work | agent:ops, no-code, operator-step |
| DRE-5531 | Backlog | agent:engineer, operator-step |
| DRE-5530 | Hand-work | agent:engineer, operator-step |
| DRE-5174 | Backlog | agent:devops, needs-human, no-code, operator-step |
| DRE-4863 | Backlog | agent:ops, needs-human, no-code, operator-step |
| DRE-4710 | Hand-work | agent:ops, no-code, operator-step |
| DRE-4672 | Done | agent:ops, no-code, operator-step |
| DRE-4541 | Hand-work | operator-step |
| DRE-4434 | Intake | agent:ops, needs-human, no-code, operator-step |
| **DRE-4267** (epic) | In Progress | agent:planner. **No `operator-step`**: removed by bureau-tools at 12:49:35 PT |
| **DRE-4198** (epic) | In Progress | Bug, agent:devops, agent:planner. **No `operator-step`**: removed by bureau-tools at 10:57:48 PT |
| DRE-3721 | Backlog | agent:ops, needs-human, no-code, operator-step |
| **DRE-3919** (epic) | Intake | agent:planner. **No `operator-step`**: removed by bureau-tools at 12:49:34 PT |
| **DRE-3711** (epic) | Intake | agent:planner. **No `operator-step`**: removed by bureau-tools at 12:49:33 PT |

**Code cards (11).**

| Card | What the migration did | Lane now (15:57 PT) | Promotion receipt into Todo |
|--|--|--|--|
| DRE-6209 | restamped FLEET, Hand-work → Backlog | Done | `🧹 Auto-promoted Backlog → Todo: parent epic active and all blockers Done.`, read on the card by this run; 11:07:35 PT. Logged `DRE-6209 → Todo` at 11:07:42 PT in run [37970787194](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/37970787194). Merged as agent-bureau #3435 at 11:24 PT |
| DRE-6210 | restamped FLEET, Hand-work → Backlog | Done | the same receipt text, read on the card by this run; 11:07:36 PT. Logged `DRE-6210 → Todo` at 11:07:42 PT in the same run. Merged as agent-bureau #3437 at 11:36 PT |
| DRE-6375 | Hand-work → Planning (no critic pass) | Done, built by hand, wearing `hand-built` again since 10:57:45 PT (row 5) | not restamped, so none is owed |
| DRE-6402 | label removed only (In Review) | Done | not restamped |
| DRE-5656 | label removed only (Intake) | Intake | not restamped |
| DRE-5657 | label removed only (Intake) | Intake | not restamped |
| DRE-4829 | label removed only (Intake) | Intake | not restamped |
| DRE-4417 | label removed only (Intake) | Intake | not restamped |
| DRE-4415 | label removed only (Intake) | Intake | not restamped |
| DRE-4391 | label removed only (Intake) | Intake | not restamped |
| DRE-2994 | label removed only (Intake) | Intake | not restamped |

That makes 31 proofs, 26 operator steps and epics, and 11 code cards, 68 in all, which matches the apply's `changed 68 card(s)`.

**A note on the sweep's wording.** Until #847 (DRE-6424) merged at 12:24 PT, the watchdog's line for a card in Hand-work read `watchdog: DRE-6211 is labeled 'hand-built' — …` (agent-bureau, 11:44 PT), whether or not the card wore the label. DRE-6211 did not wear it: bureau-tools had removed it at 10:36:53 PT. The sweeps' lines now name the real mark, for example `is a PROOF: card` and `is labeled 'operator-step'` at 15:53 PT. The `0 hand-built (nothing dispatched)` counter in the promotion summary still uses the old word.

## Amended by hand, 2026-10-09 14:15–14:30 PT

The merge gate declined this record at 11:15 PT on the four rows above (DRE-6141). An operator-session helper then re-read the rows that later events could settle. It did not use this run's identities, so they are named here. **Nothing was moved, labeled or posted to make an observation happen.**

- **Identities used for the re-reads:**
  - **The console database:** `make db-read` (read-only by construction; `transaction_read_only = on`), under the operator's AWS profile. It read the `card` and `event` tables: states, `no_code`, label snapshots and receipts.
  - **Linear:** the operator-tools key (`bureau-tools`), read-only. It read the label history of DRE-6375, DRE-3722, DRE-6140, DRE-6135, DRE-5313, DRE-5312, DRE-3611, DRE-6475, DRE-6463 and DRE-6384, plus a paged query, run twice, for every DRE issue that carries `hand-built` today. That came to 43 requests in all.
  - **GitHub:** the operator session's `gh` login, read-only, for run logs, job steps and merge times.
- **Row 4, the restamped code cards:** observed, and amended in the row and in the code-card table above.
- **Row 5, the 48-hour window:** amended in the row. The query was every DRE issue carrying `hand-built` at 14:27 PT (archived ones included), with each one's label history, plus the database's label snapshots since 05:07 PT. Five cards in an open lane still carry the label, but all five are archived or trashed, so the live board's count is still 0: DRE-6140 and DRE-6135 (sandbox fixtures, archived 10-07), DRE-5313 and DRE-5312 (trashed 10-03) and DRE-3611 (archived 10-03). One limit: a card created with the label after 10:58 PT that later lost it would show in neither source.
- **Row 1 is left as this run judged it.** Two later facts bear on it:
  - The proof-dispatch step runs on **full passes only**, on purpose. reconcile.yml's `Dispatch proof runs` step is `if: inputs.sweep_reason == ''`. Its comment reads "FULL PASSES ONLY … so a relay-scoped `card-done` or `epic-activated` pass never runs it". That condition shipped with the dispatch itself (DRE-5926, `e61d247`, 2026-10-06 01:31 PT).
  - When a full pass does the promoting, the same run names the card. Scheduled bureau-pipeline run [37970925711](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37970925711) (`sweep-scope: full pass (schedule)`) logged `DRE-5196 → Hand-work` at 11:07:19 PT. At 11:07:44 PT, in its `Dispatch proof runs` step (`success`), it logged `proof-dispatch: DRE-5196 — dispatched: first proof run (dispatch 1 of 2)`.
  - The dispatch takes one proof per pass. DRE-6211 went into Hand-work at 11:37 PT, and its `🔬 proof-run` receipt says it was dispatched at 13:54 PT. DRE-6042 (11:55 PT) was dispatched by agent-bureau run 37993210332 at 14:26:25 PT, and that run logged `DRE-6211 — deferred — one dispatch per pass, read next pass`.
- **Row 2 is left as this run judged it.** Three more cards went into Hand-work after this run read the logs: DRE-5196 (11:07:19 PT), DRE-6211 (11:37:34 PT) and DRE-6042 (11:55:43 PT). All three are `PROOF:` cards, and each receipt names only `no-code`. No OPERATOR card has been promoted. DRE-6408, now In Progress, is the open card for one reason why: an operator card filed with `needs-human` never leaves Backlog.
- **Row 4's DRE-3722 is unchanged.** It still reads `no_code = 0` in the database and has no `no-code` label in Linear. Its only label change is still `- hand-built` by `bureau-tools` at 10:38:11 PT. (Overtaken at 14:38 PT: see row 4.)
