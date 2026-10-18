# PROOF record — DRE-7200: PROOF: bureau-pipeline — a card the plan critic parks in Triage is repaired and back in Planning without a person (epic DRE-7174)

**Status: PARTIAL.**

The repair is switched on and running, but it has had nothing to repair yet. `TRIAGE_REPAIR_LIVE` went on at 2026-10-17 22:33 PT. Two scheduled Triage Repair runs have fired since then, at 22:40 PT and 22:50 PT. Both logged `triage-repair: event=schedule dry_run=false`, and neither selected a card. The Triage lane held no plan-critic park at 22:50 PT, so no park has happened since the switch.

The card says what happens next. Every judged row waits for the first plan-critic park in Triage that is repaired as `rewritten`, `split` or `replanned`, so the card is held for the operator under a `🔬 proof-waiting` hold naming that event. The operator records the event when it happens, and the next run reads every row on this branch. If no such park happens by 2026-10-31 22:33 PT, 14 days after the switch, the operator asks the CEO once whether to keep waiting another 14 days or to close this card on the parks the record lists.

## How this was recorded

- Run: proof run 39101825817 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/39101825817, started 2026-10-17 22:48 PT on `main` at `594a7ed4` (`594a7ed47ccb0e975cac52bbff6ebeeae0c0940b`). This is the first proof run on this card, so the branch `agent/DRE-7200-proof-record` was created off `main`.
- Release observed: the card says the Triage Repair schedule runs from `main` in this repo, so for it the merge is the release. All six build cards' pull request merges are ancestors of `a4fbfb78d`, the commit the first live run ran on, read with `git merge-base --is-ancestor`:
  - DRE-7177 — #1089, `586e165c`
  - DRE-7178 — #1090, `1576c50c`
  - DRE-7179 — #1100, `0f7c5e00`
  - DRE-7181 — #1099, `b28fa629`
  - DRE-7182 — #1110, `3c572b07`
  - DRE-7183 — #1098, `bb2c0b07`
- The switch: DRE-7199's operator comment, `Operator session, 10-17 10:33 PM PT.`, records `gh variable set TRIAGE_REPAIR_LIVE --body true -R dreadnought-foundry/bureau-pipeline` as run at **2026-10-17 22:33 PT**. The read token cannot read the variable (Identities, below), so the switch time is the one that comment records, as the card says. The first live run's log shows it from the run's side: `TRIAGE_REPAIR_LIVE: true` in the step env, then `triage-repair: event=schedule dry_run=false`.
- Observation window of this run: **2026-10-17 22:49 PT** (first read) to **2026-10-17 22:53 PT** (last read).
- How the Triage lane was read: `python3 scripts/triage_repair.py select --out /tmp/proof-select.json`, run at 2026-10-17 22:50 PT with this run's Linear key. `select` writes nothing (its help reads `decide each card's repair; writes nothing`). It prints one `triage-repair: <CARD> → <action>` line for every card in scope, meaning in Triage, wearing `needs-human`, with `plan-critic-bound` as its newest live stamp. It printed no such line and wrote `[]`.
- Every time below is Pacific. Nothing was written to any card but this one's heartbeats and its actor marker.
- proof local run: none — `PROOF_LOCAL_STATUS` is `none`, and the card has no `Local screen:` row.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** The token holds contents, actions, pull-requests and metadata read, and does not hold `variables: read`. `GH_TOKEN=$GH_READ_TOKEN gh variable get TRIAGE_REPAIR_LIVE -R dreadnought-foundry/bureau-pipeline` answered `failed to get variable TRIAGE_REPAIR_LIVE: HTTP 403: Resource not accessible by integration`, which is the narrow mint (`variables:read not granted to this installation`). This run used it for every GitHub read: the Triage Repair workflow's run list, the jobs of runs 39101376714 and 39101947099, and the `Select the cards` job log of each. The one refused write, made at 2026-10-17 22:51 PT:

      GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs \
        -f ref=refs/heads/proof-write-probe-39101825817 -f sha=594a7ed47ccb0e975cac52bbff6ebeeae0c0940b

  GitHub's answer: `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}` — `gh: Resource not accessible by integration (HTTP 403)`. No ref was created.
- **GitHub, written: `GH_TOKEN`.** Used only to push this branch and open the record's pull request.
- **Linear: `LINEAR_API_KEY`, the fleet key.** Used for reads of DRE-7199's comments and of the Triage lane (through `select`, above), and for this card's heartbeats and actor marker. Nothing else was written, and no card was moved. linear requests: 14 of 40.
- **AWS:** an AWS session was present in the run's env. This card needs none of the product's identities, so the run made no AWS call and no hosted sign-in.

## Triage Repair runs since the switch

Every Triage Repair run created after 2026-10-17 22:33 PT, read from the `Select the cards` job log of each. The `Repair ${{ matrix.card }}` job was skipped in both.

| Run | Started (PT) | Commit | Log lines | Cards claimed |
| -- | -- | -- | -- | -- |
| 39101376714 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/39101376714) | 2026-10-17 22:40:48 | `a4fbfb78d` | `triage-repair: event=schedule dry_run=false` and no `triage-repair: <CARD> →` line | none |
| 39101947099 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/39101947099) | 2026-10-17 22:50:40 | `594a7ed47` | `triage-repair: event=schedule dry_run=false` and no `triage-repair: <CARD> →` line | none |

The run before the switch, 39100735141 at 22:29:45 PT, is DRE-7199's dry run (`dry_run=true`) and is not counted.

## Criteria

| Criterion | Result |
| -- | -- |
| **The card.** The park named under "Which park closes it". Its Linear thread shows these 5 lines, in this order and each with its PT time: the `🛑` park note; the `🔒 hold: reason=plan-critic-bound` stamp; `🩹 triage-repair: started`; `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py`; the outcome receipt, naming every finding of the park note by its number with an answer beside it. The record quotes each line, and lists every earlier park after the switch with its outcome. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: the card's thread with the `🛑` park note, the `🔒 hold: reason=plan-critic-bound` stamp, `🩹 triage-repair: started`, the `🔓 hold lifted: reason=plan-critic-bound because=triage-repair by=triage_repair.py` line and the outcome receipt answering each numbered finding, each quoted with its PT time. So far: no park after the switch. `triage_repair.py select` at 2026-10-17 22:50 PT printed no card in scope and wrote `[]`, and neither live run claimed a card, so there is no earlier park to list. |
| **Back in Planning.** That card's move into Planning, read from its Linear history, is no more than 30 minutes after its park note. Its next `plan-critic:` record follows the move. Both are recorded with PT times. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: that card's move into Planning, read from its Linear history no more than 30 minutes after its park note, and its next `plan-critic:` record after the move, both with PT times. So far: no card has been repaired, so no card has moved. |
| **The run.** The Triage Repair run that claimed that card carries the lines `triage-repair: event=schedule dry_run=false`, `triage-repair: <CARD> → <action>` and `triage-repair: <CARD> → <outcome>` in its log. The record quotes them with the run id and its start time in PT. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: the run that claimed it, its id and PT start time, with `triage-repair: event=schedule dry_run=false`, `triage-repair: <CARD> → <action>` and `triage-repair: <CARD> → <outcome>` quoted from its log. So far: runs 39101376714 (22:40:48 PT) and 39101947099 (22:50:40 PT) each logged `triage-repair: event=schedule dry_run=false` and no card line. |
| **Once per card.** Every Triage Repair run between the switch going on and the record is read from its log: the cards it claimed and each outcome. No card is claimed by two runs. | Not observed. waiting for a plan-critic park in Triage repaired as rewritten, split or replanned: every Triage Repair run from 22:33 PT to the record, read from its log, with the cards each claimed and each outcome, and no card claimed by two runs. So far: both runs since the switch, 39101376714 and 39101947099, are read in the table above, and neither claimed a card. |
| The CEO closes this card after reading the record | Open: the CEO's step |
