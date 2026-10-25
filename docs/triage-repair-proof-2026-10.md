# PROOF record — DRE-7200: PROOF: bureau-pipeline — a card the plan critic parks in Triage is repaired and back in Planning without a person (epic DRE-7174)

**Status: PARTIAL.**

The repair is switched on and has run, but no park has closed this card yet. `TRIAGE_REPAIR_LIVE` went on at 2026-10-17 22:33 PT. Since then 581 scheduled Triage Repair runs have fired, and every one logged `triage-repair: event=schedule dry_run=false`. Exactly one claimed a card: run 40140307305 claimed DRE-7902 on 2026-10-24, and its outcome was `asked`. The planner found a choice only the CEO could make and asked it as one question. Under "Which park closes it", an `asked` outcome does not close this card, so DRE-7902 is listed below as an earlier park and the wait goes on to the next park.

Every judged row therefore waits for the first plan-critic park in Triage repaired as `rewritten`, `split` or `replanned`. The card is held for the operator under a `🔬 proof-waiting` hold naming that event. If no such park happens by 2026-10-31 22:33 PT, 14 days after the switch, the operator asks the CEO once whether to keep waiting another 14 days or to close this card on the parks the record lists.

## How this was recorded

- Run: shadow proof run 40229342068 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/40229342068, started 2026-10-25 13:08 PT on `main` at `2dee2574` (`2dee257461cac762f3cd66f3fbeb09c38bfd7349`), model claude-sonnet-5-5. It is the second run of this card's re-check at `f68da44`, run beside the real proof run (40229341287), which owns the card, its record branch and its pull request. This run wrote this one file and nothing else, made no heartbeat and no sandbox press.
- Release observed: the card says the Triage Repair schedule runs from `main` in this repo, so for it the merge is the release. All six build cards' merges (DRE-7177 #1089, DRE-7178 #1090, DRE-7179 #1100, DRE-7181 #1099, DRE-7182 #1110, DRE-7183 #1098) are ancestors of the commit the first live run ran on, as the earlier record on `agent/DRE-7200-proof-record` read with `git merge-base --is-ancestor`.
- The switch: DRE-7199's operator comment records `gh variable set TRIAGE_REPAIR_LIVE --body true` as run at **2026-10-17 22:33 PT**. The read token cannot read the variable (Identities, below), so the switch time is the one that comment records, as the card says. Every run's log shows it from the run's side: `triage-repair: event=schedule dry_run=false`.
- Observation window of this run: **2026-10-25 13:08 PT** to **2026-10-25 13:15 PT**.
- How the runs were read: the workflow's run list (`actions/workflows/383373616/runs`, created at or after 2026-10-17 22:33 PT) returned 581 runs, all `schedule`, all `success`, the first created 2026-10-17 22:40:48 PT and the last read created 2026-10-25 12:50:43 PT. For each run the `Select the cards` job log was read in full, and the `Repair <card>` job log where one ran. No step summary was read.
- How the thread was read: `python3 .bureau-pipeline/scripts/linear_ops.py dump-comments DRE-7902 --with-authors`, which prints each comment with its creation time in UTC. The times below are those, converted to Pacific.
- Every time below is Pacific.
- proof local run: none — `PROOF_LOCAL_STATUS` is `none`, and the card has no `Local screen:` row.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** Used for every GitHub read: the Triage Repair workflow's run list, each run's jobs, and each job's log. The token does not hold `variables: read`, so the switch variable is not read from GitHub. The one refused write, made at 2026-10-25 13:11 PT:

      GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs \
        -f ref=refs/heads/proof-write-probe-40229342068 -f sha=2dee257461cac762f3cd66f3fbeb09c38bfd7349

  GitHub's answer: `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}` — `gh: Resource not accessible by integration (HTTP 403)`. No ref was created.
- **GitHub, written: `GH_TOKEN`.** Not used by this run. A shadow run pushes nothing and opens no pull request.
- **Linear: `LINEAR_API_KEY`, the fleet key, read-only here (`BUREAU_LINEAR_READ_ONLY=1`).** Used for two reads: this card's thread and DRE-7902's thread. Nothing was written to any card. linear requests: 2 of 40.
- **AWS:** a session was present in the run's env. This card needs none of the product's identities, so the run made no AWS call and no hosted sign-in.

## Earlier parks since the switch

Each park after 2026-10-17 22:33 PT that a Triage Repair run claimed, with its outcome. A park the schedule's `select` step skipped as out of scope prints no line in its log, so the logs list claimed parks only. Of the 581 runs, 580 printed no `triage-repair: <CARD> →` line and their `Repair ${{ matrix.card }}` job was skipped.

| Park (PT) | Card | Outcome | Closes this card? |
| -- | -- | -- | -- |
| 2026-10-24 15:46:29 | DRE-7902 — agent-bureau: onboarding onboard-sandbox-2 stopped at 50_retire_route | `asked` | No. `asked` does not close it. |

DRE-7902's thread, from `dump-comments`, in order:

- 2026-10-24 15:35:47 — a first `🛑` note at round 2 (6 findings). It was not a park in Triage: the same minute a `🔁 bound-rewrite` comment gave the planner its one extra rewrite, and a fresh planning attempt started at 15:36:37.
- 2026-10-24 15:46:29 — the park note: `🛑 **Pre-approval critic — before this is built** — two send-backs on this card — the bound. …` followed by `Every finding raised on this card so far (8), oldest first`, findings 1 to 8.
- 2026-10-24 15:46:31 — `🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml`.
- 2026-10-24 15:51:46 — `🩹 triage-repair: started — this card was parked in Triage by the plan critic's bound, and the planner is now answering every finding of its park note once, without a person. This happens once per card. …`
- 2026-10-24 15:53:21 — `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py`.
- 2026-10-24 15:53:22 — `🙋 planning-escalation: DRE-7902 needs a decision from you before it can be planned …`, one question: should the card stay open to track the operator's re-run, or be canceled.
- 2026-10-24 15:53:23 — `🩹 triage-repair: asked — the planner found a choice on this card only the CEO can make, so it is asked as one question in Green Light rather than guessed, and the card's description is unchanged. This happens once per card.`
- 2026-10-24 17:42:33 — the CEO's signed answer from the console: cancel the card. 2026-10-24 17:42:34 — `Moved to Canceled`.

DRE-7902 never moved back into Planning after the park, so it gives the "Back in Planning" row nothing.

An operator note on this card (2026-10-23 11:18 PT) says the sandbox drill card DRE-7788 passed both plan critics on round 1 and was never parked in Triage. That is a person's comment in Linear, read here, and this run did not open DRE-7788.

## Triage Repair runs since the switch

Read from the `Select the cards` job log of each of the 581 runs. Every run logged exactly one `triage-repair: event=schedule dry_run=false`. Only one logged a card line.

| Run | Started (PT) | Log lines | Cards claimed |
| -- | -- | -- | -- |
| 39101376714 (the first live run) | 2026-10-17 22:40:48 | `triage-repair: event=schedule dry_run=false` | none |
| 40140307305 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/40140307305) | 2026-10-24 15:51:12 | `triage-repair: event=schedule dry_run=false`; `triage-repair: DRE-7902 → repair — all 8 finding(s) are the planner's to answer in a rewrite`; `triage-repair: DRE-7902 → started`; `triage-repair: DRE-7902 → asked` | DRE-7902, outcome `asked` |
| the other 579 runs, 2026-10-17 22:50:40 to 2026-10-25 12:50:43 | — | `triage-repair: event=schedule dry_run=false` and no `triage-repair: <CARD> →` line | none |

In run 40140307305 the `event=schedule dry_run=false` line is stamped 2026-10-24 15:51:18, the `→ repair` line 15:51:20, the `→ started` line 15:51:46 and the `→ asked` line 15:53:23. DRE-7902 was claimed by this one run and by no other.

## Criteria

| Criterion | Result |
| -- | -- |
| **The card.** The park named under "Which park closes it". Its Linear thread shows these 5 lines, in this order and each with its PT time: the `🛑` park note; the `🔒 hold: reason=plan-critic-bound` stamp; `🩹 triage-repair: started`; `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py`; the outcome receipt, naming every finding of the park note by its number with an answer beside it. The record quotes each line, and lists every earlier park after the switch with its outcome. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: the card's thread with the `🛑` park note, the `🔒 hold: reason=plan-critic-bound` stamp, `🩹 triage-repair: started`, the `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py` line and the outcome receipt answering each numbered finding, each quoted with its PT time. So far: one park since the switch, DRE-7902 (park note 2026-10-24 15:46:29 PT), outcome `asked`, which does not close the card. It is listed under "Earlier parks since the switch". |
| **Back in Planning.** That card's move into Planning, read from its Linear history, is no more than 30 minutes after its park note. Its next `plan-critic:` record follows the move. Both are recorded with PT times. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: that card's move into Planning, read from its Linear history no more than 30 minutes after its park note, and its next `plan-critic:` record after the move, both with PT times. So far: no card has been repaired as `rewritten`, `split` or `replanned`, so no card has moved. DRE-7902 went to Green Light on its `asked` receipt and was canceled at 2026-10-24 17:42 PT. |
| **The run.** The Triage Repair run that claimed that card carries the lines `triage-repair: event=schedule dry_run=false`, `triage-repair: <CARD> → <action>` and `triage-repair: <CARD> → <outcome>` in its log. The record quotes them with the run id and its start time in PT. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: the run that claimed it, its id and PT start time, with `triage-repair: event=schedule dry_run=false`, `triage-repair: <CARD> → <action>` and `triage-repair: <CARD> → <outcome>` quoted from its log. So far: run 40140307305 (2026-10-24 15:51:12 PT) carries all three shapes for DRE-7902, with outcome `asked`, which is not a closing outcome. |
| **Once per card.** Every Triage Repair run between the switch going on and the record is read from its log: the cards it claimed and each outcome. No card is claimed by two runs. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: every Triage Repair run from 22:33 PT to the record, read from its log, with the cards each claimed and each outcome, and no card claimed by two runs. So far: all 581 runs since the switch are read in the table above. One card, DRE-7902, was claimed, by one run, 40140307305. |
