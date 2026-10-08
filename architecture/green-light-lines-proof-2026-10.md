# PROOF record — DRE-6200: PROOF: the three lines reach the CEO's queue — the live Green Light rows read and checked, the four cases rendered from the merged fixtures, and stable read after the epic's last merge, recorded by the dispatched proof run (epic DRE-3893)

**Status: PASS.**

## How this was recorded

- **Run:** Proof Task run 37855046223, attempt 1 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37855046223 — started 2026-10-08 15:41 PT. Dispatched as the first proof run.
- **Release observed:** the `stable` tag in `dreadnought-foundry/bureau-pipeline` (`.github/bureau/release.json`, surface `pipeline-channel`, `tag_series: ["stable"]`), read at 2026-10-08 15:43 PT and again at 2026-10-08 15:46 PT. Both reads returned `fdc6c766692ee9c305718bbbe58df752708e366f`.
- **Commit observed:** `fdc6c766692ee9c305718bbbe58df752708e366f`, the released commit. A detached worktree of that commit ran the fixture renders and the lane-contract reads (`green_light_rows.arrivals()` and `green_light_rows.kinds()`). This run's own checkout of `main` is at `abbddbd8`, which is one merge past `stable` (#821, DRE-6186, which is outside this epic).
- **The epic's last merge.** The epic has 16 children. Eight build cards are Done, each with one merged pull request in this repository. I read them as the read identity with `gh pr list --state merged --search "DRE-<n>"`:

  | Card | Pull request | Merged (PT) | Merge commit |
  | -- | -- | -- | -- |
  | DRE-3908 | #794 | 2026-10-07 17:01 | `a1c181fe` |
  | DRE-5204 | #796 | 2026-10-07 17:29 | `44cdfa9f` |
  | DRE-6196 | #798 | 2026-10-07 17:31 | `0ab275ec` |
  | DRE-3909 | #799 | 2026-10-07 18:07 | `0626b34a` |
  | DRE-3910 | #801 | 2026-10-07 19:32 | `0bd0a949` |
  | DRE-6174 | #807 | 2026-10-08 11:34 | `5ef13868` |
  | DRE-3911 | #817 | 2026-10-08 14:50 | `7408bb13` |
  | DRE-3915 | #820 | 2026-10-08 15:25 | `fdc6c766` |

  The other eight children are Canceled: DRE-3912, DRE-3913, DRE-3914, DRE-3916, DRE-3917, DRE-3920, DRE-6175 and DRE-6197. I read this from `linear_ops.py children-json DRE-3893`. So the epic's last merge is #820, merge commit `fdc6c766692ee9c305718bbbe58df752708e366f`, at 2026-10-08 15:25 PT.
- **When `stable` reached it.** Promote run 37854510045 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37854510045) logged `harness-passed-promoting: promoting stable to fdc6c766692ee9c305718bbbe58df752708e366f — harness green, strictly ahead.` with `CHANNEL_HEAD: 1016894c…`. Its `Move the channel` step returned the ref at `fdc6c766…` at 2026-10-08 15:36:24 PT. That is the release boundary every Green Light row below is measured against.
- **Green Light read at:** 2026-10-08 15:43:37 PT, two minutes after the run started.

## Identities

- **github read:** `GH_READ_TOKEN`. The step summary's line reads `proof identity: github read — app agent-bureau-bot-3, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. I used it to read the `stable` ref, the `compare` between `fdc6c766` and `stable`, the merged pull requests of the build cards, the promote-channel run list and logs, and the planning run logs in `dreadnought-foundry/bureau-pipeline` and `dreadnought-foundry/portico` that moved the Green Light rows. **Refused write:** `GH_TOKEN=$GH_READ_TOKEN gh api --method POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-37855046223 -f sha=fdc6c766692ee9c305718bbbe58df752708e366f`. GitHub answered `{"message":"Resource not accessible by integration",…,"status":"403"}` (exit 1). A read of `git/ref/heads/proof-write-probe-37855046223` afterwards returned `404`, so no ref was created and there was nothing to delete.
- **github write:** `GH_TOKEN`, the worker token. I used it only to push `agent/DRE-6200-proof-record` and open this record's pull request. No observation was made with it.
- **linear:** `LINEAR_API_KEY`, the fleet key. I used it for the heartbeats and the actor marker on DRE-6200, and to read cards. The reads were the epic's children, the Green Light lane through `linear_ops.gql_paged` with `board_snapshot.CARD_QUERY` and `states: ["Green Light"]`, the threads of the three rows through `dump-comments --with-authors`, and DRE-6201's state, labels and relations. I wrote no card. **linear requests: 15 of 40** at this record's commit. That count is summed from each invocation's `linear-calls:` line. The `⏳ 5/5 PR opened` heartbeat and the actor marker are posted after the commit and are not in it.
- **aws:** none. The step summary reads `proof identity: aws — none, PROOF_ROLE_ARN not provided`. No criterion here needs AWS.
- **Scratch state:** none created.

## Criteria

| Criterion | Result |
| -- | -- |
| Observed against the live board, as the run's own Linear identity: every row in Green Light at the run's start is listed with its card id, its `repo:` label, its kind and the lane-contract marker that kind was read from (or `moved by hand, no pipeline writer`), the comment that parked it, its time in PT and whether it was posted before or after `stable` reached this epic's last merge; for each row of a decision kind (`question` or `agent-escalation`) parked after the release by a writer on a ref that carries this epic, the parking comment is quoted verbatim and the exit code and output of `python3 scripts/console_escalation.py check` on it are in the record, with exit 0 on every such row; a `passed-plan`, `queued-epic` or `weekly-report` row parked after the release is listed as `not a decision row, not checked` and is never run through `check`; a decision row parked from a pinned release tag that predates this epic's last merge is listed with that tag as not checked; a row parked before the release is listed as predating it; an empty lane is listed as empty with the time read. | Met. At 2026-10-08 15:43:37 PT the fleet key read three rows in Green Light: DRE-6288 (`repo:bureau-pipeline`), DRE-5233 (`repo:portico`) and DRE-4710 (`repo:portico`). Each row's kind is `question`, read from the `🙋 planning-escalation:` marker. That is the `question` arrival `planning_escalation.py#escalate` on the released commit. Each row was last moved into the lane between 12:24 and 12:26 PT, by `planning_escalation.py escalate` logging `already escalated, under planning-escalation`. Each parking note was posted between 00:41 and 12:07 PT. All of those times are before `stable` reached `fdc6c766` at 15:36 PT. So all three rows are listed as **predating the release, not checked**. No row was parked after the release, so no row of any kind needed `check`, and none was run through it. Every row is quoted under "Green Light rows as read" below. |
| The record quotes the four fixture cases (DRE-3879, DRE-3885, DRE-3887, DRE-3889) as rendered through `console_escalation.py render` from the merged fixture file on the released commit, all four with their Finding, Question and Recommendation lines. | Met. Each of the four records of `tests/fixtures/green-light-escalations-2026-09-14.json` (blob `fd14e13f` at `fdc6c766`) was rendered through `python3 scripts/console_escalation.py render` (blob `64fa5985`) on the released commit. All four exited 0 and printed a Finding, a Question and a Recommendation line. They are quoted under "The four fixture cases" below. |
| The record names the `stable` sha observed after the epic's final merge and the merge commit it points at, read off the live tag. | Met. `GH_TOKEN=$GH_READ_TOKEN gh api repos/dreadnought-foundry/bureau-pipeline/git/ref/tags/stable` returned `"object":{"sha":"fdc6c766692ee9c305718bbbe58df752708e366f","type":"commit"}` at 15:43 PT and the same sha at 15:46 PT. That is the merge commit of #820 (DRE-3915, `Merge pull request #820 from dreadnought-foundry/agent/DRE-3915-green-light-writers-declare`, parents `1016894c`, `db2fa52b`), the epic's last merge. `compare/fdc6c766…...stable` answered `status: identical`, `ahead_by: 0`, `behind_by: 0`. |
| The record names the deferred card that owes the first real park (DRE-6201) and says why that observation is not a row here. | Met. See "The deferred observation" below. DRE-6201 was read at this run: title `bureau-pipeline: the first real Green Light park after DRE-3893's release, read by hand — the deferred observation its proof cannot wait for (DRE-3075)`, state `Backlog`, labels `repo:bureau-pipeline`, `initiative:bureau`, `agent:devops`, `needs-human`, `no-code`. |
| The record is merged to `main` in `architecture/green-light-lines-proof-2026-10.md`. | Not observed. This pull request is that merge, and a run cannot observe its own pull request merging before it ends. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## Green Light rows as read

The lane was read at **2026-10-08 15:43:37 PT** with `linear_ops.gql_paged(board_snapshot.CARD_QUERY, {"states": ["Green Light"]})` (one request). It returned three rows. Each thread was read with `linear_ops.py dump-comments <id> --with-authors`. Every comment on all three threads reads `authored_by_pipeline: true`, so none of the three is `moved by hand, no pipeline writer`.

Release boundary: `stable` reached `fdc6c766` at **2026-10-08 15:36:24 PT**.

**How each row was matched.** The lane contract on the released commit declares the kinds `passed-plan`, `question`, `agent-escalation`, `queued-epic` and `weekly-report`. Its `question` arrival is `planning_escalation.py#escalate`, with the evidence `the planning-escalation note, stated in business terms or refused by planning_escalation.refusal, posted before the move`.

**The parking comment, and why it is not the newest one.** For all three rows, the newest pipeline-authored comment before the latest move into the lane is a `🎟️ planner-slot: claimed` line. That line matches no arrival. The run that made the move logged `<id>: already escalated, under planning-escalation`, then `<id> → Green Light`. So the move reused the card's standing `🙋 planning-escalation:` note instead of posting a new one. That note is the parking comment matched and quoted here.

| Card | `repo:` | Kind | Marker matched | Parking note posted (PT) | Last moved into Green Light (PT), by | vs. release | Checked |
| -- | -- | -- | -- | -- | -- | -- | -- |
| DRE-6288 | `repo:bureau-pipeline` | `question` | `🙋 planning-escalation:` | 2026-10-08 00:41:18 | 2026-10-08 12:26:25, `self-plan.yml` run [37831870491](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37831870491) at `d681dcb8` | before — predates the release | not checked, predates the release |
| DRE-5233 | `repo:portico` | `question` | `🙋 planning-escalation:` | 2026-10-08 11:29:22 | 2026-10-08 12:25:26, portico `plan.yml` run [37831691428](https://github.com/dreadnought-foundry/portico/actions/runs/37831691428) at `2f2c6108` | before — predates the release | not checked, predates the release |
| DRE-4710 | `repo:portico` | `question` | `🙋 planning-escalation:` | 2026-10-08 12:07:45 | 2026-10-08 12:24:21, portico `plan.yml` run [37831600618](https://github.com/dreadnought-foundry/portico/actions/runs/37831600618) at `450c06dc` | before — predates the release | not checked, predates the release |

All three cards had already been in Green Light earlier the same day. The CEO's latest console answers moved them to Planning: DRE-4710 at 12:23 PT, DRE-5233 at 12:24 PT and DRE-6288 at 12:25 PT. Each card's next planning run then parked it again. The release came about three hours after the last re-park. For the two portico rows, the pinned `pipeline_ref` was not read: a row parked before the release is listed as predating it, whatever ref its writer ran. The parking notes are quoted verbatim below. They are quoted for the record, not run through `check`.

### DRE-6288 — parking note, 2026-10-08 00:41:18 PT

```
🙋 planning-escalation: DRE-6288 needs a decision from you before it can be planned — the reasoning itself is the deliverable here, and that part is not work an agent can do.

🔎 Finding: This card asks for a decision rather than for work: there is nothing to build until somebody chooses, so no amount of planning moves it.
❓ Question: Is this something you want to settle yourself, or should we put it back in the queue as it stands?
💡 Recommendation: none given — the run that parked this card stated no recommendation

**Why it needs you:** This card asks for a decision rather than for work: there is nothing to build until somebody chooses, so no amount of planning moves it. Should we start paying for and using Anthropic's new Claude Haiku 5.5 model line in our review process, given it is a different product from the models we already run and we have no price on record for it?

This card is parked in **Green Light** — your decision queue, the same place a plan waits for you. It is not broken and it has not failed anything; it is correct and waiting on judgement.

Answer it here and move the card back to be picked up, or park it if we should not do this at all.
```

### DRE-5233 — parking note, 2026-10-08 11:29:22 PT

```
🙋 planning-escalation: DRE-5233 needs a decision from you before it can be planned — the reasoning itself is the deliverable here, and that part is not work an agent can do.

🔎 Finding: This card asks for a decision rather than for work: there is nothing to build until somebody chooses, so no amount of planning moves it.
❓ Question: Is this something you want to settle yourself, or should we put it back in the queue as it stands?
💡 Recommendation: none given — the run that parked this card stated no recommendation

**Why it needs you:** This card asks for a decision rather than for work: there is nothing to build until somebody chooses, so no amount of planning moves it. When a reader highlights a passage in a document that nobody has commented on yet, should they be offered a 'Suggest an edit' button beside 'Comment' (as the old comment rail did), so they can propose replacement wording directly without first writing a comment — and if so, what should that control look like?

This card is parked in **Green Light** — your decision queue, the same place a plan waits for you. It is not broken and it has not failed anything; it is correct and waiting on judgement.

Answer it here and move the card back to be picked up, or park it if we should not do this at all.
```

### DRE-4710 — parking note, 2026-10-08 12:07:45 PT

```
🙋 planning-escalation: DRE-4710 needs a decision from you before it can be planned — the reasoning itself is the deliverable here, and that part is not work an agent can do.

🔎 Finding: This card asks for a decision rather than for work: there is nothing to build until somebody chooses, so no amount of planning moves it.
❓ Question: Is this something you want to settle yourself, or should we put it back in the queue as it stands?
💡 Recommendation: none given — the run that parked this card stated no recommendation

**Why it needs you:** This card asks for a decision rather than for work: there is nothing to build until somebody chooses, so no amount of planning moves it. When one of your moderators uses an AI assistant to read a customer's form answers, should that customer have to agree to it first? Choose one: no consent needed (how it works today); the moderator records the customer organisation's consent before their answers can be read this way; or each person gives consent on the form itself when they answer it.

This card is parked in **Green Light** — your decision queue, the same place a plan waits for you. It is not broken and it has not failed anything; it is correct and waiting on judgement.

Answer it here and move the card back to be picked up, or park it if we should not do this at all.
```

## The four fixture cases

Each record of `tests/fixtures/green-light-escalations-2026-09-14.json` was rendered on the released commit `fdc6c766` by passing its fields to the CLI:

    python3 scripts/console_escalation.py render --finding <finding> --question <question> --recommendation <recommendation> --why <recommendation_why>

All four exited 0 with nothing on stderr. Each output is quoted verbatim below.

### DRE-3879 (one-off-critic QUESTION) — exit 0

```
🔎 Finding: The card offers two different fixes for the same problem without picking one, and choosing between them is a security policy call, not a coding task.
❓ Question: Should the job open a pull request, or be let past branch protection?
💡 Recommendation: open a pull request — a bypass loosens the repository's security for one job, and a pull request keeps every change reviewed
```

### DRE-3885 (one-off-critic QUESTION) — exit 0

```
🔎 Finding: The card names two ways to exempt the nightly standards-sync pull request from the test-first rule and does not pick one.
❓ Question: Exempt it by branch and author, or by the paths it touches?
💡 Recommendation: by branch and author — a path rule would also exempt hand-written changes to the same files, and branch plus author names exactly the bot's own pull requests
```

### DRE-3887 (one-off-critic QUESTION) — exit 0

```
🔎 Finding: One of the four checklist items can only be proven true after the change is already approved and live, so nobody can ever tick that box.
❓ Question: Drop the after-merge item from this card, or keep it and accept the card can never close on its own?
💡 Recommendation: move it to the follow-on card — DRE-3875 already waits for this card to merge, so it is the card that can actually watch the stable tag move
```

### DRE-3889 — exit 0

```
🔎 Finding: the critic produced no result
❓ Question: Re-run the review, or settle the card yourself?
💡 Recommendation: re-run the review — the critic returned no result, and a no-result is a crash, not a rejection
```

## The deferred observation

`deferred: the first real Green Light park after the release — the proof run cannot wait on chance, and a record with a Not observed. row does not merge (DRE-6141)`.

DRE-6201 owes it. This run cannot wait for the pipeline to happen to park a card for a decision. A row that could read `Not observed.` on the day the run reads the lane would hold this record unmerged. So the first real park after `stable` carries this epic is the operator's to read on DRE-6201. The operator runs that park's comment through `console_escalation.py check` and records the card id, the writer, the three lines and the check output on DRE-6201 before closing it.

When this run read the lane at 15:43 PT, that park had not happened yet. All three rows in Green Light were parked before the release at 15:36 PT. So DRE-6201 still owes the observation.

DRE-6201's state and labels as read are in the criterion table above. It is blocked by DRE-3909, DRE-3910, DRE-3911, DRE-3915, DRE-5204, DRE-6174, DRE-6196 and DRE-6197. It has no `blockedBy` relation to DRE-3908, which is also a Done build card of this epic.
