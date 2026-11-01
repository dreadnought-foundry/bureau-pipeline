# PROOF record — DRE-7200: PROOF: bureau-pipeline — a card the plan critic parks in Triage is repaired and back in Planning without a person (epic DRE-7174)

**Status: PASS.**

The repair worked on a real card with no person involved. DRE-8570 was parked in Triage by the plan critic at 2026-10-31 21:28:22 PT. Triage Repair run 41273786368 claimed it at 21:37 PT, the planner rewrote it once and answered all six findings of the park note, and the card was back in Planning at 21:39:50 PT, 11 minutes after the park. The plan critic passed the rewritten card at 21:41:39 PT, and it went on to build and merge the same night.

DRE-8570 is the park "Which park closes it" names. It is the first in-scope park after the switch went on (2026-10-17 22:33 PT) whose outcome is `rewritten`, `split` or `replanned`, and no `🩹 triage-repair:` comment stands before it. The one earlier park, DRE-7902 on 2026-10-24, came out `asked`, which does not close this card. All 1,181 Triage Repair runs from the switch to 2026-11-01 14:50 PT were read from their logs. Four cards were claimed, each by exactly one run.

## How this was recorded

- Run: proof run 41359022100 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41359022100, started 2026-11-01 14:49:06 PT on `main` at `99086676` (`990866767a3af7aacb35d56d273af18421e05abc`). It checked out this branch, merged `origin/main` into it and amended this record in a new commit.
- Why this run was dispatched: `split: 1 row(s) cannot recur unattended (proof-overdue of 2026-11-01)`. The card's newest `⏳ proof-overdue:` comment, posted 2026-11-01 14:18:47 PT, names the row **The card.** as its `waits-on`, read "by elimination" as something that will not happen again by itself. That premise did not hold. The event the row waits for had already happened, at 2026-10-31 21:28 PT, before that comment was posted. The proof run's job is to write what it saw, so every row below is the reading itself, and none is written as history.
- Earlier runs on this card: proof runs 39101825817 (started 2026-10-17 22:48 PT), 40229341287 (2026-10-25 13:08 PT), 40304089542 (2026-10-26 03:01 PT) and 40417341449 (2026-10-26 16:05 PT). Each found no closing park. This run did not rely on their log readings. It read all 1,181 runs again itself.
- Release observed: the card says the Triage Repair schedule runs from `main` in this repo, so for it the merge is the release. The first run read all six build cards' merges as ancestors of `a4fbfb78d`, the commit the first live run ran on: DRE-7177 (#1089, `586e165c`), DRE-7178 (#1090, `1576c50c`), DRE-7179 (#1100, `0f7c5e00`), DRE-7181 (#1099, `b28fa629`), DRE-7182 (#1110, `3c572b07`) and DRE-7183 (#1098, `bb2c0b07`). The run that claimed DRE-8570 ran on `c26607641`, a later commit of `main`.
- The switch: DRE-7199's operator comment, posted 2026-10-17 22:33 PT and opening `Operator session, 10-17 10:33 PM PT.`, records `gh variable set TRIAGE_REPAIR_LIVE --body true -R dreadnought-foundry/bureau-pipeline` as run at **10:33 PM PT**. Runs 40229341287 and 40304089542 read that comment, and this run did not read it again. The read token cannot read the variable (Identities, below), so the switch time is the one that comment records, as the card says. Every one of the 1,181 runs since logged `dry_run=false`, so the switch has stayed on.
- Observation window of this run: **2026-11-01 14:50 PT** (first read) to **2026-11-01 14:55 PT** (last read).
- How the runs were read: the Triage Repair workflow (`.github/workflows/triage-repair.yml`, id 383373616) run list, filtered to runs created at or after 2026-10-17 22:33 PT and read in two windows, because GitHub's list stops at 1,000. `total_count` was 432 for runs created 2026-10-17 22:33 PT to 2026-10-23 17:00 PT, and 749 for runs created after that through 2026-11-01. That is 1,181 runs, all `schedule`, all on `main`, all `completed` and `success`. Then every run's job list, then the log of every job that was not skipped: 1,181 `Select the cards` logs, 4 `Repair DRE-<n>` logs and 8 `List the cards the repair could not fix` logs. Each line quoted below is from those logs. No step summary was read for a run.
- How the card was read: `python3 .bureau-pipeline/scripts/linear_ops.py dump-comments DRE-8570 --with-authors` for its thread, and `linear_ops.lane_history('DRE-8570', 50)` for its lane moves off Linear's issue history, both read at 2026-11-01 14:53 PT with this run's Linear key.
- The criterion `The CEO reads the record and closes this card.` gets no row: `proof_record.is_closing_row` reads it as closing, and the card closes on its approved, all-met record (DRE-7773).
- Every time below is Pacific: daylight time through 2026-11-01 02:00, standard time after. Nothing was written to any card but this one's heartbeats and its actor marker.
- proof local run: none — `PROOF_LOCAL_STATUS` is `none`, and the card has no `Local screen:` row.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** `proof identity: github read — app agent-bureau-bot-2, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. `GH_TOKEN=$GH_READ_TOKEN gh variable get TRIAGE_REPAIR_LIVE -R dreadnought-foundry/bureau-pipeline` answered `failed to get variable TRIAGE_REPAIR_LIVE: HTTP 403: Resource not accessible by integration`. This run used the token for every GitHub read: its own run's start, the workflow list, the Triage Repair run list, the job lists of all 1,181 runs and the 1,193 logs named above. The one refused write, made at 2026-11-01 14:53 PT:

      GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs \
        -f ref=refs/heads/proof-write-probe-41359022100 -f sha=ed200f7b078cb6d96f3f1274bc0706d8e20571ea

  GitHub's answer: `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}` — `gh: Resource not accessible by integration (HTTP 403)`. No ref was created.
- **GitHub, written: `GH_TOKEN`.** Used only to push this branch.
- **Linear: `LINEAR_API_KEY`, the fleet key.** Used to read this card's thread, DRE-8570's thread and DRE-8570's lane history, and for this card's heartbeats and actor marker. Nothing else was written, and no card was moved. linear requests: 17 of 40, counting the heartbeats and the actor marker posted after the push.
- **AWS:** `proof identity: aws — none, PROOF_ROLE_ARN not provided`. This card needs none of the product's identities, and the run made no hosted sign-in.

## Parks after the switch

Every in-scope park the Triage Repair runs printed since 2026-10-17 22:33 PT, in order.

| Card | Park note (PT) | Outcome (PT) | Closes this card |
| -- | -- | -- | -- |
| DRE-7902 | 2026-10-24 15:46:29 | `asked`, 2026-10-24 15:53:23 | No — an `asked` outcome does not close it |
| DRE-8570 | 2026-10-31 21:28:22 | `rewritten`, 2026-10-31 21:39:51 | Yes — the first `rewritten`, `split` or `replanned` park, with no `🩹 triage-repair:` comment before it |

DRE-7902's five lines, read by proof run 40229341287 and kept from the earlier record: the `🛑` park note at 15:46:29 PT with 8 numbered findings, `🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml` at 15:46:31 PT, `🩹 triage-repair: started` at 15:51:46 PT, `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py` at 15:53:21 PT, and `🩹 triage-repair: asked` at 15:53:23 PT. It was moved to Canceled from the console at 2026-10-24 17:42 PT.

After the closing park the logs show three more cards. None changes which park closes this card:

- DRE-8562 and DRE-8563 were both claimed by run 41343766180 (started 2026-11-01 11:50:27 PT). Its log reads `triage-repair: DRE-8563 → started` (11:51:00 PT), `triage-repair: DRE-8563 → rewritten` (11:52:10 PT), `triage-repair: DRE-8562 → started` (11:52:37 PT) and `triage-repair: DRE-8562 → rewritten` (11:56:24 PT).
- DRE-8486 was in scope and skipped by `select` in 28 runs, from 41307309456 (2026-11-01 04:36:36 PT) to 41341278954 (11:20:56 PT): `triage-repair: DRE-8486 → spent — the planner has had its one bound rewrite on this epic — a `bound-rewrite` receipt stands on its thread, so this park is the second`. No run claimed it.

The operator's drill card DRE-7788, filed 2026-10-23 11:18 PT, never appears in any run's log.

## DRE-8570's thread and lane history

DRE-8570 was a one-off card about a false alarm in Foundry's nightly sweep. Its thread, read with `linear_ops.py dump-comments DRE-8570 --with-authors`, in order, every line posted by the pipeline:

- 21:20:54 PT — an earlier `🛑` note at round 2, followed at 21:20:56 PT by `🔁 bound-rewrite:` and a fresh planning attempt rather than a hold. The card was not in Triage then: its lane history shows no Triage entry before 21:28:25 PT.
- **21:28:22 PT — the park note:** `🛑 **Pre-approval critic — before this is built** — two send-backs on this card — the bound. The planner's revision did not satisfy the critic, so the card parks in Triage for the operator with every finding raised so far named below.` It lists `Every finding raised on this card so far (6)`, numbered 1 to 6.
- 21:28:23 PT — `plan-critic: stage=one-off round=4 result=SEND_BACK collisions=0 run=41273124417 — …`
- **21:28:24 PT — the stamp:** `🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml`
- **21:37:20 PT —** `🩹 triage-repair: started — this card was parked in Triage by the plan critic's bound, and the planner is now answering every finding of its park note once, without a person. This happens once per card.` It quotes the description as it stood before the repair. It is the card's first `🩹 triage-repair:` comment.
- **21:39:50 PT —** `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py`
- **21:39:51 PT — the outcome receipt:** `🩹 triage-repair: rewritten — the planner rewrote this card once to answer every finding of its park note, the hold is lifted, and the card is back in Planning, where a fresh plan run has the critics read the rewritten card. This happens once per card. The findings, each with its answer:` Then findings 1 to 6, each quoted from the park note, each followed by an `Answer:` line. For example, finding 4 is followed by `Answer: Removed the sentence "the card is not ready to build until both are set." …`, and finding 5 by `Answer: The card now says plainly that it needs no agent label before it builds. …`.
- 21:41:39 PT — `plan-critic: stage=one-off round=5 result=PASS collisions=0 run=41273967463 — Read the card against the shared pipeline's code: the slot-check bug, both red-exit points and the existing tests are described accurately, and the wrong-repository label now has a working hand-off route.`

Its lane moves, from Linear's issue history (`linear_ops.lane_history`), all on 2026-10-31 PT:

| Time (PT) | Moved to |
| -- | -- |
| 21:28:25 | Triage |
| 21:39:50 | Planning |
| 21:41:41 | Queued |
| 21:49:28 | In Progress |
| 21:59:15 | Queued |
| 22:17:50 | In Progress |
| 22:26:47 | In Review |
| 22:52:06 | Done |

The card's later thread shows a `wrong-repo` blocker at 21:59:14 PT, the sweep's relabel to `repo:bureau-pipeline` at 22:09:15 PT, and pull request #1330 merged at 22:51:54 PT.

## Triage Repair runs since the switch

Every Triage Repair run created at or after 2026-10-17 22:33 PT: 1,181 runs, all `schedule`, all on `main`, all concluding `success`. The first is 39101376714, started 2026-10-17 22:40:48 PT on `a4fbfb78d`. The newest is 41359147924, started 2026-11-01 14:50:38 PT on `990866767`. Every one of the 1,181 `Select the cards` logs carries `triage-repair: event=schedule dry_run=false`.

| Runs | Log lines | Cards claimed |
| -- | -- | -- |
| 1,138 runs | no `triage-repair: <CARD> →` line, and the `Repair ${{ matrix.card }}` job skipped | none |
| 40140307305 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/40140307305), started 2026-10-24 15:51:12 PT on `02102a623` | `Select the cards`: `triage-repair: DRE-7902 → repair — all 8 finding(s) are the planner's to answer in a rewrite`. `Repair DRE-7902`: `triage-repair: DRE-7902 → started`, `triage-repair: DRE-7902 → asked` | DRE-7902, outcome `asked` |
| 41273786368 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41273786368), started 2026-10-31 21:36:58 PT on `c26607641` | `Select the cards`: `triage-repair: DRE-8570 → repair — all 6 finding(s) are the planner's to answer in a rewrite`. `Repair DRE-8570`: `triage-repair: DRE-8570 → started`, `triage-repair: DRE-8570 → rewritten` | DRE-8570, outcome `rewritten` |
| 28 runs, 41307309456 (2026-11-01 04:36:36 PT) to 41341278954 (11:20:56 PT) | `triage-repair: DRE-8486 → spent — …`, and the repair job skipped | none |
| 41343766180 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41343766180), started 2026-11-01 11:50:27 PT on `cfccedde0` | `Select the cards`: `triage-repair: DRE-8563 → repair — all 1 finding(s) …` and `triage-repair: DRE-8562 → repair — all 6 finding(s) …`. `Repair DRE-8563`: `→ started`, `→ rewritten`. `Repair DRE-8562`: `→ started`, `→ rewritten` | DRE-8563 and DRE-8562, both `rewritten` |
| 12 runs, 41345617602 (2026-11-01 12:12:05 PT) to 41359147924 (14:50:38 PT) | `triage-repair: DRE-8563 → spent — the card has had its one automatic repair — a `rewritten` receipt stands on its thread` and the same for DRE-8562, and the repair job skipped | none |

Across all 1,193 logs, each of `triage-repair: DRE-7902 → started`, `DRE-8570 → started`, `DRE-8562 → started` and `DRE-8563 → started` appears exactly once. Only 4 `Repair DRE-<n>` jobs ran, one per card, and the other 1,178 repair jobs were skipped. No card was claimed by two runs. The 8 `List the cards the repair could not fix` jobs, in the newest runs, printed lines such as `triage-unrepaired: 0 card(s) listed · complete=yes · outcome=resolve-refused`.

## Criteria

| Criterion | Result |
| -- | -- |
| **The card.** The park named under "Which park closes it". Its Linear thread shows these 5 lines, in this order and each with its PT time: the `🛑` park note; the `🔒 hold: reason=plan-critic-bound` stamp; `🩹 triage-repair: started`; `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py`; the outcome receipt, naming every finding of the park note by its number with an answer beside it. The record quotes each line, and lists every earlier park after the switch with its outcome. | Met. The park is DRE-8570, the first in-scope park after the 2026-10-17 22:33 PT switch to come out `rewritten`, with no `🩹 triage-repair:` comment before it. Its thread, read with `linear_ops.py dump-comments DRE-8570 --with-authors` at 2026-11-01 14:53 PT, shows the five lines in order on 2026-10-31: `🛑 **Pre-approval critic — before this is built** — two send-backs on this card — the bound. …` at 21:28:22 PT, listing findings 1 to 6; `🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml` at 21:28:24 PT; `🩹 triage-repair: started — …` at 21:37:20 PT; `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py` at 21:39:50 PT; and `🩹 triage-repair: rewritten — … The findings, each with its answer:` at 21:39:51 PT, which quotes findings 1 to 6 by number, each with an `Answer:` line beside it. Each line is quoted under "DRE-8570's thread and lane history". The one earlier park, DRE-7902 (2026-10-24 15:46:29 PT, outcome `asked`), is listed under "Parks after the switch". |
| **Back in Planning.** That card's move into Planning, read from its Linear history, is no more than 30 minutes after its park note. Its next `plan-critic:` record follows the move. Both are recorded with PT times. | Met. DRE-8570's lane history, read with `linear_ops.lane_history('DRE-8570', 50)` at 2026-11-01 14:53 PT, shows `Triage` at 2026-10-31 21:28:25 PT and `Planning` at 21:39:50 PT. That is 11 minutes 28 seconds after the park note at 21:28:22 PT. The next `plan-critic:` record follows the move at 21:41:39 PT: `plan-critic: stage=one-off round=5 result=PASS collisions=0 run=41273967463 — …`. |
| **The run.** The Triage Repair run that claimed that card carries the lines `triage-repair: event=schedule dry_run=false`, `triage-repair: <CARD> → <action>` and `triage-repair: <CARD> → <outcome>` in its log. The record quotes them with the run id and its start time in PT. | Met. Run 41273786368 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41273786368), started 2026-10-31 21:36:58 PT on `main` at `c26607641`, event `schedule`. Its `Select the cards` log carries `triage-repair: event=schedule dry_run=false` (21:37:04 PT) and `triage-repair: DRE-8570 → repair — all 6 finding(s) are the planner's to answer in a rewrite` (21:37:05 PT). Its `Repair DRE-8570` log carries `triage-repair: DRE-8570 → started` (21:37:20 PT) and `triage-repair: DRE-8570 → rewritten` (21:39:51 PT). |
| **Once per card.** Every Triage Repair run between the switch going on and the record is read from its log: the cards it claimed and each outcome. No card is claimed by two runs. | Met. All 1,181 Triage Repair runs created from 2026-10-17 22:33 PT to the record were read by this run from their logs: 1,181 `Select the cards` logs, each with `triage-repair: event=schedule dry_run=false`, plus every job that was not skipped. The first is 39101376714 (2026-10-17 22:40:48 PT), and the newest is 41359147924 (2026-11-01 14:50:38 PT). Four cards were claimed, each by one run: DRE-7902 by 40140307305 (`asked`), DRE-8570 by 41273786368 (`rewritten`), and DRE-8563 and DRE-8562 by 41343766180 (both `rewritten`). Each `→ started` line appears exactly once, only 4 repair jobs ran, and later runs printed `→ spent` for DRE-8562, DRE-8563 and DRE-8486 and claimed nothing. No card was claimed by two runs. The table is under "Triage Repair runs since the switch". |
