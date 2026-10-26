# PROOF record — DRE-7200: PROOF: bureau-pipeline — a card the plan critic parks in Triage is repaired and back in Planning without a person (epic DRE-7174)

**Status: PARTIAL.**

The repair is live and has claimed one card, but that park closes nothing. `TRIAGE_REPAIR_LIVE` went on at 2026-10-17 22:33 PT. Since then 649 scheduled Triage Repair runs have fired, through 2026-10-26 15:52 PT, and every one logged `triage-repair: event=schedule dry_run=false`. One of them claimed a card: run 40140307305, at 2026-10-24 15:51 PT, claimed DRE-7902. That park's outcome was `asked`, and under "Which park closes it" an `asked` outcome does not close this card. No other card was in scope in any run, and the Triage lane held no plan-critic park at 2026-10-26 16:08 PT.

The card says what happens next. Every judged row waits for the first plan-critic park in Triage that is repaired as `rewritten`, `split` or `replanned`, so the card stays held for the operator under a `🔬 proof-waiting` hold naming that event. The operator records the event when it happens, and the next run reads every row on this branch. If no such park happens by 2026-10-31 22:33 PT, 14 days after the switch, the operator asks the CEO once whether to keep waiting another 14 days or to close this card on the parks the record lists.

## How this was recorded

- Run: proof run 40417341449 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/40417341449, started 2026-10-26 16:05 PT on `main` at `6f66363b` (`6f66363b58a748a9599c054c42ec7df74ac73568`). It was dispatched as a re-check of the waiting rows at `b582187`, the head of this branch. It checked the branch out, merged `origin/main` into it, and amended this record in a new commit.
- Earlier runs on this card: proof run 39101825817, started 2026-10-17 22:48 PT (the first), proof run 40229341287, started 2026-10-25 13:08 PT, which read the first 581 Triage Repair runs from their logs, and proof run 40304089542, started 2026-10-26 03:01 PT, which read the next 36, through 40304144392. Their readings are kept below where they still hold. This run read the 32 runs created after 40304144392 from their logs.
- Release observed: the card says the Triage Repair schedule runs from `main` in this repo, so for it the merge is the release. The first run read all six build cards' merges as ancestors of `a4fbfb78d`, the commit the first live run ran on: DRE-7177 (#1089, `586e165c`), DRE-7178 (#1090, `1576c50c`), DRE-7179 (#1100, `0f7c5e00`), DRE-7181 (#1099, `b28fa629`), DRE-7182 (#1110, `3c572b07`) and DRE-7183 (#1098, `bb2c0b07`). Every run since started on a later commit of `main`.
- The switch: DRE-7199's operator comment, posted 2026-10-17 22:33 PT and opening `Operator session, 10-17 10:33 PM PT.`, records `gh variable set TRIAGE_REPAIR_LIVE --body true -R dreadnought-foundry/bureau-pipeline` as run at **10:33 PM PT**. Runs 40229341287 and 40304089542 read that comment; this run did not read it again. The read token cannot read the variable (Identities, below), so the switch time is the one that comment records, as the card says. Every run since logged `dry_run=false`, so the switch has stayed on.
- Observation window of this run: **2026-10-26 16:07 PT** (first read) to **2026-10-26 16:08 PT** (last read).
- How the runs were read: the Triage Repair workflow's run list, filtered to runs created at or after 2026-10-17 22:33 PT (`total_count` 649 at 2026-10-26 16:07 PT, and none created after 40415718979 at 16:08 PT), then every run's job list, then the log of every job that was not skipped: 649 `Select the cards` logs and 1 `Repair DRE-7902` log, across the three proof runs named above. This run's `triage-repair: DRE-[0-9]* →` search was checked against run 40140307305's `Select the cards` log, where it found the one `DRE-7902 → repair` line. Each line quoted below is from those logs. No step summary was read for a run.
- How the Triage lane was read: `python3 scripts/triage_repair.py select --out /tmp/proof-select.json`, run at 2026-10-26 16:08 PT with this run's Linear key. `select` writes nothing (its help reads `decide each card's repair; writes nothing`). It prints one `triage-repair: <CARD> → <action>` line for every card in scope, meaning in Triage, wearing `needs-human`, with `plan-critic-bound` as its newest live stamp. It printed no such line and wrote `[]`.
- The criterion `The CEO reads the record and closes this card.` gets no row: `proof_record.is_closing_row` reads it as closing, and the card closes on its approved, all-met record (DRE-7773). The first run's record had kept a row for it, and this run took that row out.
- Every time below is Pacific. Nothing was written to any card but this one's heartbeats and its actor marker.
- proof local run: none — `PROOF_LOCAL_STATUS` is `none`, and the card has no `Local screen:` row.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** `proof identity: github read — app agent-bureau-bot-2, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. `GH_TOKEN=$GH_READ_TOKEN gh variable get TRIAGE_REPAIR_LIVE -R dreadnought-foundry/bureau-pipeline` answered `failed to get variable TRIAGE_REPAIR_LIVE: HTTP 403: Resource not accessible by integration`. This run used the token for every GitHub read: the Triage Repair run list, the jobs of the 32 new runs and their 32 `Select the cards` logs, run 40140307305's job list and `Select the cards` log, and this run's own start time. The one refused write, made at 2026-10-26 16:08 PT:

      GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs \
        -f ref=refs/heads/proof-write-probe-40417341449 -f sha=b58218719753f94f04edac7efcd44b8b42734735

  GitHub's answer: `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}` — `gh: Resource not accessible by integration (HTTP 403)`. No ref was created.
- **GitHub, written: `GH_TOKEN`.** Used only to push this branch.
- **Linear: `LINEAR_API_KEY`, the fleet key.** Used to read the Triage lane (through `select`, above) and for this card's heartbeats and actor marker. DRE-7902's and DRE-7199's comments were read by run 40229341287 and are quoted from its record. Nothing else was written, and no card was moved. linear requests: 13 of 40, counting the 5/5 heartbeat and the actor marker posted after the push.
- **AWS:** `proof identity: aws — none, PROOF_ROLE_ARN not provided`. This card needs none of the product's identities, and the run made no hosted sign-in.

## Parks after the switch

Every in-scope park the Triage Repair runs printed since 2026-10-17 22:33 PT. There is one.

| Card | Park note (PT) | Outcome (PT) | Closes this card |
| -- | -- | -- | -- |
| DRE-7902 | 2026-10-24 15:46:29 | `asked`, 2026-10-24 15:53:23 | No — an `asked` outcome does not close it |

DRE-7902's thread, read with `linear_ops.py dump-comments DRE-7902 --with-authors`, in order:

- 15:46:29 PT — `🛑 **Pre-approval critic — before this is built** — two send-backs on this card — the bound. The planner's revision did not satisfy the critic, so the card parks in Triage for the operator with every finding raised so far named below.` It lists 8 numbered findings. An earlier `🛑` note at 15:35:47 PT was followed by the `🔁 bound-rewrite:` comment at 15:35:50 PT, not by a hold, so the card was not parked in Triage then.
- 15:46:31 PT — `🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml`
- 15:51:46 PT — `🩹 triage-repair: started — this card was parked in Triage by the plan critic's bound, and the planner is now answering every finding of its park note once, without a person.` No `🩹 triage-repair:` comment stands before the park note.
- 15:53:21 PT — `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py`
- 15:53:23 PT — `🩹 triage-repair: asked — the planner found a choice on this card only the CEO can make, so it is asked as one question in Green Light rather than guessed, and the card's description is unchanged. This happens once per card.`

The card was moved to Canceled from the console at 2026-10-24 17:42 PT.

The operator's drill card DRE-7788, filed 2026-10-23 11:18 PT, never appears in any run's log, which matches the operator's reading on this card that it passed both plan critics and was never parked.

## Triage Repair runs since the switch

Every Triage Repair run created at or after 2026-10-17 22:33 PT: 649 runs, all `schedule`, all on `main`, all concluding `success`. The first is 39101376714, started 2026-10-17 22:40:48 PT on `a4fbfb78d`. The newest is 40415718979, started 2026-10-26 15:52:23 PT on `6f66363b5`.

| Runs | Log lines | Cards claimed |
| -- | -- | -- |
| 648 runs | `triage-repair: event=schedule dry_run=false` in the `Select the cards` log, no `triage-repair: <CARD> →` line, and the `Repair ${{ matrix.card }}` job skipped | none |
| 40140307305 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/40140307305), started 2026-10-24 15:51:12 PT on `02102a623` | `Select the cards`: `triage-repair: event=schedule dry_run=false` (15:51:18 PT), `triage-repair: DRE-7902 → repair — all 8 finding(s) are the planner's to answer in a rewrite` (15:51:20 PT). `Repair DRE-7902`: `triage-repair: DRE-7902 → started` (15:51:46 PT), `triage-repair: DRE-7902 → asked` (15:53:23 PT) | DRE-7902, outcome `asked` |

Across all 650 logs, `triage-repair: DRE-7902 → started` appears once, and no other card line appears at all. No card was claimed by two runs.

## Criteria

| Criterion | Result |
| -- | -- |
| **The card.** The park named under "Which park closes it". Its Linear thread shows these 5 lines, in this order and each with its PT time: the `🛑` park note; the `🔒 hold: reason=plan-critic-bound` stamp; `🩹 triage-repair: started`; `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py`; the outcome receipt, naming every finding of the park note by its number with an answer beside it. The record quotes each line, and lists every earlier park after the switch with its outcome. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: the card's thread with the `🛑` park note, the `🔒 hold: reason=plan-critic-bound` stamp, `🩹 triage-repair: started`, the `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py` line and the outcome receipt answering each numbered finding, each quoted with its PT time. So far: one park after the switch, DRE-7902, parked 2026-10-24 15:46:29 PT, outcome `asked` at 15:53:23 PT, listed with its five lines under "Parks after the switch". `asked` does not close this card. `triage_repair.py select` at 2026-10-26 16:08 PT printed no card in scope and wrote `[]`. |
| **Back in Planning.** That card's move into Planning, read from its Linear history, is no more than 30 minutes after its park note. Its next `plan-critic:` record follows the move. Both are recorded with PT times. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: that card's move into Planning, read from its Linear history no more than 30 minutes after its park note, and its next `plan-critic:` record after the move, both with PT times. So far: the one repaired park, DRE-7902, was `asked`, so it went to the CEO's question rather than back to Planning, and it was canceled at 2026-10-24 17:42 PT. |
| **The run.** The Triage Repair run that claimed that card carries the lines `triage-repair: event=schedule dry_run=false`, `triage-repair: <CARD> → <action>` and `triage-repair: <CARD> → <outcome>` in its log. The record quotes them with the run id and its start time in PT. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: the run that claimed it, its id and PT start time, with `triage-repair: event=schedule dry_run=false`, `triage-repair: <CARD> → <action>` and `triage-repair: <CARD> → <outcome>` quoted from its log. So far: the one run that claimed a card, 40140307305 (started 2026-10-24 15:51:12 PT), logged `triage-repair: event=schedule dry_run=false`, `triage-repair: DRE-7902 → repair — all 8 finding(s) are the planner's to answer in a rewrite` and `triage-repair: DRE-7902 → asked`, for the park that does not close this card. |
| **Once per card.** Every Triage Repair run between the switch going on and the record is read from its log: the cards it claimed and each outcome. No card is claimed by two runs. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: every Triage Repair run from 2026-10-17 22:33 PT to the record, read from its log, with the cards each claimed and each outcome, and no card claimed by two runs. So far: all 649 runs through 40415718979 (2026-10-26 15:52:23 PT) are read from their logs in the table above. 648 claimed no card, and 40140307305 claimed DRE-7902 once, outcome `asked`. |
