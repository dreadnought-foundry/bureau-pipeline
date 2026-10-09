# PROOF record — DRE-6274: PROOF: holds carry their reason, lift themselves within one pass and send the card back where the pipeline resumes it, reach a person only as a Green Light question, and no Done or Canceled card carries needs-human — observed on the live board (epic DRE-6172)

**Status: PASS.**

The holds lane cleared the stale labels: 553 closed cards lost `needs-human` over fourteen hourly passes, 40 at a time, each with a `hyg-hold-cleared` receipt. One closed card carried the label when this run read Linear, and it closed after the finishing pass. Live cases arose for three of the four mechanisms. A real `unfixable-check` hold was lifted by a new head in the very next pass and sent back to In Review. A real spent review budget became a Green Light question that passes the three-line check. No `no-route` hold and no dead-run hand-off arose in the window, so those two rows are met by dry runs on the released commit, quoted below. Every time is Pacific.

| Criterion | Result |
|---|---|
| The record names, for each of the thirteen `repo:bureau-pipeline` build cards (DRE-6173, DRE-6177, DRE-6178, DRE-6179, DRE-6180, DRE-6181, DRE-6182, DRE-6186, DRE-6189, DRE-6190, DRE-6247, DRE-6248, DRE-6273), the tag that carries its merge, read with `python3 scripts/proof_release.py check --repo dreadnought-foundry/bureau-pipeline` as the run's read-only GitHub token with the `proof-release:` lines quoted; names the PT time that release went live as the opening of the window, and the run's own start as its close; and every observation below is dated inside that window. | Met. `stable` carries all thirteen merges. The check was read as `GH_READ_TOKEN` at 2026-10-09 12:07:30 PT and printed thirteen `proof-release: ready — pipeline-channel (the whole repository): stable carries #<pr> (<sha>) for DRE-<n>` lines, exit 0 (all quoted in §1). The last build merge is DRE-6181's #827 (`8e37315`), merged 2026-10-08 18:52:53 PT. `stable` is a moving lightweight tag. Its release record shows it first carried `8e37315` at **2026-10-08 19:01:18 PT**: Promote Channel run [37872551328](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37872551328) printed `harness-passed-promoting: promoting stable to 8e37315f…` and `channel-record: release recorded: deployment 6951114346 (stable@8e37315)`, and that deployment record was created at 2026-10-08 19:01:18 PT. That time opens the window. It closes at **2026-10-09 12:05:45 PT**, when this run ([37977855483](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37977855483)) started. Every row below is judged on board facts dated inside that window. The one exception is the history row 3 asks for: its first four passes (14:43, 15:44, 16:43 and 18:19 PT on 2026-10-08) ran before the window opened, on `main` commits that carried DRE-6180's lane but not `8e37315`. They are listed because row 3 asks for every pass from the first. No row is met on them. |
| For the two `repo:agent-bureau` cards (DRE-6176, DRE-6198), whose row opens `Met. on the default branch —`, the record quotes the GitHub compare of agent-bureau's default branch against each merge commit answering `identical` or `behind` (`channel_record.CARRIED`), read as the run's read-only token, and quotes the `proof-release:` lines of `python3 scripts/proof_release.py check --repo dreadnought-foundry/agent-bureau` for both merges, saying in one sentence whether each was carried by a named tag, untouched by every release surface, or not yet carried by the console's release — and that no observation in this record depends on the console's deploy; a compare or release record GitHub refused to serve reads `Not observed.` with its answer. | Met. on the default branch — the compare was read as `GH_READ_TOKEN` at 2026-10-09 12:09:34 PT, with default branch `main`. `compare/main...4a0bcc1e…` (DRE-6176, #3407) answered `behind ahead_by=0 behind_by=148`. `compare/main...f34b67f4…` (DRE-6198, #3408) answered `behind ahead_by=0 behind_by=145`. Both answers are in `channel_record.CARRIED`. The release check printed: `proof-release: ready — console: newest agent-bureau-console-v1.6.364 carries #3407 (4a0bcc1) for DRE-6176`; `proof-release: ready — console: newest agent-bureau-console-v1.6.364 carries #3408 (f34b67f) for DRE-6198`; and `ready — website: untouched`, `ready — relay: untouched` and `ready — relay-gh: untouched` for both merges, exit 0. Both merges were carried by the named tag `agent-bureau-console-v1.6.364`, and no observation in this record depends on the console's deploy. |
| Observed from Linear as the fleet key the proof run holds, read only: the count of Done or Canceled cards carrying `needs-human` after the pass that finished the backlog — the first pass whose ledger carries no carried-over row for the lane — is zero (`Met. zero —`), or every card it counts is listed by id with the PT time it entered Done or Canceled, read off its own history, later than the finishing pass's PT time (`Met. by listing —`) — a card that closed after the pass carries the label until the next hourly pass through no fault of the lane, and the next pass lifts it; a counted card that was closed before the finishing pass is a miss and the row reads `Not met.` naming it — with the Linear query quoted with its `includeArchived: true` argument; every pass from the first live pass with the holds lane to the finishing one is listed with its run id and PT time; the per-pass cap in force is named; the number of cards lifted is counted off their `hyg-hold-cleared` receipts and set beside the 207 counted on 2026-10-07; three of the lifted cards are named with their receipt quoted; and the number of lifted cards that were archived in Linear is stated, read off the `archived` evidence of their receipts. | Met. by listing — the count is one card, and it closed after the finishing pass. I read Linear at 2026-10-09 12:12:03 PT through `linear_ops.gql_paged` as the fleet key, read only, with `issues(first: 100, after: $after, includeArchived: true, filter: { labels: { name: { eq: "needs-human" } }, state: { type: { in: ["completed", "canceled"] } } })`. It returned **DRE-6034** (Done, not archived). Its own history shows `In Review → Done` at **2026-10-09 11:50:22 PT**. The finishing pass is [37919679888](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37919679888) at 03:46:45 PT, the first whose ledger has no carried row, so the card closed after it. It also closed after the newest pass began (11:49:25 PT), so the next hourly pass lifts it. **Passes:** 14 live scheduled passes, from the first with the lane to the finishing one, are listed in §2. **Cap in force:** 40 (`HYGIENE_HOLDS_MAX_LIFTS` unset, so `DEFAULT_MAX_LIFTS = 40`), and every carried row reads `over the per-pass cap of 40 — carried to the next pass`. **Lifted:** 553 cards, each with one executed `hyg-hold-cleared` receipt: 535 on the dreadnought-foundry leg, 13 on DeltaSolv and 5 on EveryBite. All 553 were `because=card-closed` (456 Done and 79 Canceled on the home leg). That is 346 more than the **207** counted in the console database on 2026-10-07. Linear's comment search holds 521 of those receipts. The other 32 are on archived cards, which that search does not return. **Archived:** 32, read off their receipts' `archived — unarchived for this write and re-archived after it` evidence. Three receipts, read off Linear at 12:12:25 PT, are quoted in §3: DRE-3017 (Done, archived, 2026-10-08 19:57 PT), DRE-4048 (Canceled, 22:49 PT) and DRE-6194 (Canceled, 2026-10-09 03:48 PT). |
| Quoted from the operator's `🔬 proof-observed:` comment on this card, posted before the run dispatched: the open-holds report (DRE-6198's SQL file), run through `make db-read SQL=console/backend/reports/open_holds.sql` under the operator's AWS profile after the finishing pass, with its PT time, lists at least one card carrying the label with reason, age, lift kind and lane; its row count in Done or Canceled is stated, and where that count is not zero every such card is listed by id with its cause — a stamp or lift receipt timed between the operator's two reads, or a database gap named for the backfill epic that owns it — so the row is met on a zero or on that listing, never on a figure left unexplained. | Met. by listing — I read the operator's comment off this card through `linear_ops.py dump-comments DRE-6274 --with-authors` at 12:19:23 PT. It was posted 2026-10-09 10:42:18 PT, before this run was dispatched at 12:05:43 PT and after the finishing pass at 03:46 PT. It quotes `AWS_PROFILE=dreadnought make db-read SQL=console/backend/reports/open_holds.sql`, run 10:40 PT to 10:41 PT with `transaction_read_only = on`: **37 rows over every lane**. Among its rows: `DRE-6082	Green Light	review-cap-spent	a851b67d…	new-head	4.0	2` and `DRE-6404	Triage	unfixable-check	52d872d6…	new-head	0.0	1` (card, lane, reason, qualifier, lift kind, age in hours, reason total). **Rows in Done or Canceled: 1 — DRE-6231 (Done).** Its stated cause: it "entered Done at 2026-10-09 10:39 PT, two minutes before this report and after the newest hygiene run (run 37961614093, started 09:47 PT)". That fits neither cause this criterion names — it is neither a stamp or receipt between the two reads nor a database gap. It is the close-after-the-pass cause row 3 accepts. The next pass confirms it: [37968286272](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37968286272) (10:43:31 PT) lifted DRE-6231 with `hyg-hold-cleared — reason=manual because=card-closed · 10:43 PT` (§2). A second operator comment (11:53:30 PT) found the same pattern after pass 37975942933: 32 rows, 1 in Done (DRE-6034, closed 11:50 PT during that pass). |
| Quoted from the same comment: the Linear figure the operator read within the same hour as the report, with the same paged query over every state, set beside the report's row count over every lane; the two agree, or every card in one and not the other is listed by id with its cause (a stamp or `hyg-hold-cleared` receipt between the two reads, or a database gap attributed to DRE-6057's family), and the record says in one sentence which of the two it was — a difference is never averaged and never hidden, and listed with its cause it meets the row. | Met. — the comment quotes the Linear figure read 2026-10-09 10:41 PT, as the operator's key, read only, with `issues(first: 100, after: $after, includeArchived: true, filter: {labels: {name: {eq: "needs-human"}}})` followed to the last page: **37 cards over every lane**, 1 in Done or Canceled (DRE-6231). One archived card is counted (DRE-5270). That sits beside the report's **37 rows over every lane**. It says: "The report's 37 rows and Linear's 37 cards are the same 37 identifiers (a sorted diff of the two lists is empty)". The two agreed, so no card needed listing. The 11:53 PT reading agreed as well: 32 against 32, with the same identifiers. |
| One hold lifted by a condition other than closing, inside the window: either `Met. on the live board —` with its stamp's PT time, the live fact that met it, the hygiene pass that lifted it, that pass's PT time, at most one pass apart, and the lane the card was returned to; or `Met. by dry run —` saying none arose in the window and quoting the holds lane's dry run on the released commit — `HYGIENE_DRY_RUN=1` over `tests/test_hygiene_holds.py`'s fixture (DRE-6180, and DRE-6273, which adds those two lifts and their lane moves to the lane), the `would:` lines for one `new-head` lift and one `repo-on-rail` lift quoted with their lane moves. | Met. on the live board — **DRE-6404**, an `unfixable-check` hold. **Stamp:** `🔒 hold: reason=unfixable-check at=52d872d6c11b3d9a559bff20eab814ef681685f8 lifts=new-head by=agent-fix.yml`, posted 2026-10-09 10:40:27 PT. In the card's history, the label was added at 10:40:26 PT and the card moved `In Review → Triage` at 10:40:28 PT. **Live fact:** a new head, `1b867e88`, was force-pushed to agent-bureau#3431 (branch `agent/DRE-6404-tracemalloc-off-by-default`) at 10:42:07 PT, read off the pull request's timeline as `GH_READ_TOKEN`. **Lifting pass:** [37968286272](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37968286272), created 10:43:31 PT. It was the first pass after the stamp, because the pass before it began at 09:47:13 PT, so they are zero passes apart. Its log reads `DRE-6404 − label 'needs-human'`, `commented on DRE-6404` and `DRE-6404 → In Review`. The receipt, at 10:44:41 PT: `🧹 hygiene: hyg-hold-cleared — reason=unfixable-check because=new-head · 10:43 PT`, with evidence `stamp at=52d872d6…, open pull request dreadnought-foundry/agent-bureau#3431 head 1b867e88cc34a9cd37bfd63bb58da2d93403c3ec, lane Triage`. **Lane returned to:** In Review, `Triage → In Review` at 10:44:41 PT. Review resumed there: the critic posted on `1b867e88` at 10:45:48 PT, #3431 merged at 10:51:02 PT, and the card went Done at 10:52:09 PT. |
| One `no-route` hold parked in Triage by the sweep, inside the window: either `Met. on the live board —` with the card still in Triage with its label and without `hand-built` after a hygiene pass that ran while it stood there, the pass's run id and PT time quoted; or `Met. by dry run —` saying none arose in the window and quoting the Triage lane's dry run on the released commit over `tests/fixtures/hygiene-triage-2026-09-30.json` (DRE-6190), the one `Left` row for `DRE-4423` quoted. | Met. by dry run — none arose in the window. At 12:12 PT, Linear's comment search for `hold: reason=no-route` created since 2026-10-07 17:00 PT returned 0 comments. The 32 cards then carrying the label were in Backlog (22), Intake (7), Green Light (1), Done (1) and In Review (1), with none in Triage. Neither operator report listed a `no-route` row. **Dry run**, on the released commit `694eabc2` at 2026-10-09 12:13:48 PT: the Triage lane ran through `hygiene.run_leg` with `HYGIENE_DRY_RUN=1` over the fixture, using the test module's own fixture `gh` and `linear` (driver in §6). It ran with exit 0, every outcome `would`, 20 `would:` lines, and no action on DRE-4423. The one row was `Left row: {"lane": "Triage", "target": "DRE-4423", "why": "held no-route on repo:legacy-site — the slug is not on the dispatch rail", "recommendation": "correct the card's repo: label to a slug on the rail, or add the repo to config/repo-map.json if it should route; the holds lane reads the card's current label, lifts the hold and sends the card to Planning on its next pass, and nothing on the card needs clearing"}`. Also, `python3 -m pytest tests/test_hygiene_triage.py -q -k NoRoute` printed `10 passed, 43 deselected in 0.29s`. |
| One card reaching a dead-run cap arriving in Planning, inside the window: either `Met. on the live board —` with the `✂️ dead-run-cap → Planning:` receipt quoted with its PT time and naming which path wrote it; or `Met. by dry run —` saying none arose in the window and quoting, run on the released commit, `python3 -m pytest tests/test_dead_run_split_first.py tests/test_hold_sweep_dead_run.py` green (DRE-6178, DRE-6186) and `python3 scripts/dead_run.py decide 2 --comments-file <a thread with no hand-off mark>` answering `replan` with a body opening the mark, beside the same command with `--credential-expiry` answering `hold` — the hand-off is for the silent death alone. | Met. by dry run — none arose in the window. At 12:12 PT, Linear's comment search for `✂️ dead-run-cap → Planning:` since 2026-10-07 17:00 PT returned six comments, and none is a receipt. Two are critic comments on DRE-6177 and DRE-6178 (2026-10-08 14:03 and 14:04 PT), and four are plan-review comments on epic DRE-6172 (2026-10-07 17:35 to 18:08 PT). Each only quotes the phrase. **Dry run** on the released commit `694eabc2`, 2026-10-09 12:14:06 PT. `python3 scripts/dead_run.py decide 2 --comments-file /tmp/lin/thread-no-mark.json`, over a thread of two `🪦 dead-run-requeue` receipts and no hand-off mark, printed `replan` and a body opening `✂️ dead-run-cap → Planning: the dead-run cap is reached — 3 runs on this card's budget ended with no pull request, the last with no blocker note either (dead run 3/3).` The same command with `--credential-expiry` printed `hold` and `🚨 held-for-human (dead-run-requeue cap reached): the run's GitHub credential was refused before the branch could reach GitHub …`. `python3 -m pytest tests/test_dead_run_split_first.py tests/test_hold_sweep_dead_run.py -q` printed `104 passed in 183.52s (0:03:03)` (§6). |
| One card whose review budget was spent sitting in Green Light, inside the window: either `Met. on the live board —` with the three lines `python3 scripts/console_escalation.py check` accepts and the `🔒 hold: reason=review-cap-spent` stamp, the comment and the `check` output quoted with PT times — or, where DRE-3908 was lifted by hand because DRE-3893 parked, the three lines as DRE-6189 shipped them, quoted, with the record saying so; or `Met. by dry run —` saying none arose in the window and quoting, run on the released commit, `python3 -m pytest tests/test_review_cap_green_light.py tests/test_review_cap_question.py` green (DRE-6181, DRE-6189) and `console_escalation.py check` over the text `review_cap_question.compose` returns for one fixture, its output quoted. | Met. on the live board — **DRE-6353** (PR #834). **Stamp:** `🔒 hold: reason=review-cap-spent at=9950f171bd3c75d5ea2d508300960817d22db6ee lifts=new-head by=reconcile.py`, posted 2026-10-09 08:26:44 PT. **Comment**, posted 08:26:44 PT: `🚨 review-nudge-cap PR #834 @9950f171…`, carrying the three lines quoted whole in §4 (`🔎 Finding: The merge-gate re-trigger budget is spent on head 9950f17: …`, `❓ Question: Does the finding standing on this head stand and get fixed, or is the change dropped, closing the pull request and canceling the card by hand?`, `💡 Recommendation: Fix the finding — …`). **Lane:** `In Review → Green Light` at 08:26:47 PT, and the card was still in Green Light, holding the label, at 12:12:25 PT. **Check**, run at 12:19:56 PT: `python3 scripts/console_escalation.py check` over that comment exited 0 and printed nothing. A copy with the line marks stripped exited 1, naming all three lines missing. DRE-6082 was a second case with the same shape: stamp 06:41:19 PT, Green Light 06:41:22 PT, `check` exit 0. DRE-3908 was not lifted by hand. |
| Observed on the released commit, in the run's own checkout: `python3 scripts/hold.py check` printed zero problems, and the record reproduces the registry's `tried_first` table, one row per reason, naming the step each hold tries first. | Met. — the run's checkout is `694eabc220e8`, which is `stable` (read as `GH_READ_TOKEN` at 12:12:56 and 12:13:04 PT). At 12:12:47 PT, `python3 scripts/hold.py check` printed `14 hold writer site(s), 14 row(s), 0 problem(s)`, exit 0. The `tried_first` table from `config/holds.json` at that commit, one row per reason, is in §5. |
| Observed in the merge gate's Actions logs over the window, with the run's read-only GitHub token: no gate decision read or named the `needs-human` label. | Met. — 0 of 332 gate logs over the window mention `needs-human`. I downloaded every merge-gate run created between 2026-10-08 19:01:18 PT and 2026-10-09 12:05:45 PT, as `GH_READ_TOKEN`. bureau-pipeline's Self Merge Gate had 168 runs: 90 logs, plus 78 `skipped` with no job. agent-bureau's Merge Gate had 370 runs: 231 logs, plus 133 `skipped` and 6 `cancelled` whose logs GitHub answered `log not found`. portico's Merge Gate had 22 runs: 11 logs, plus 11 `skipped`. agent-bureau-demo had 0 runs. A case-insensitive search for `needs-human` matched no line in the 332 logs. Those logs carry 325 gate `decision=` lines (48 merge, 270 wait, 4 hold, 3 conflict; §7), and no `decision=` or `reason=` line contains `label`. The DeltaSolv and EveryBite repositories sit in other organizations the token does not reach (`404`), so their gates were not read. |
| The record is merged on `main` at `docs/holds-proof-2026-10.md`, opens with a criterion table whose rows answer each line above, and the CEO closes this card after reading it. | Open: this record's merge and the close happen after it is written. Read them off this record's pull request on `agent/DRE-6274-proof-record`, and off this card's thread for the close receipt. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## How this was recorded

- **The run:** `Proof Task` [37977855483](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37977855483), event `repository_dispatch`, started 2026-10-09 12:05:45 PT, dispatch reason `first proof run`. The card's `🔬 proof-run` receipt is stamped 12:05:43 PT.
- **The commit and release observed:** the run's checkout is `694eabc220e88fd726033c8b3c46d9eb5b02a215` (Merge pull request #846). `stable` read `baf000eb` at about 12:08 PT and `694eabc2` at 12:12:56 PT, and both carry `8e37315`. Every local command in this record ran at `694eabc2`.
- **The window:** opens 2026-10-08 19:01:18 PT, when `stable` first carried DRE-6181's merge `8e37315` (deployment 6951114346). It closes 2026-10-09 12:05:45 PT, at this run's start.
- **How the gate reads a row:** `python3 -c "import sys; sys.path.insert(0,'scripts'); import proof_record as p; print(p.row_met('Met. by dry run — none arose'))"` printed `True` on `694eabc2` at 12:12:56 PT.
- **The operator's observations** are the two `🔬 proof-observed:` comments on this card, at 10:42:18 PT and 11:53:30 PT. The run holds no AWS session, so it quotes them and runs no `make db-read`.
- **The hygiene passes** were read off each scheduled `Hygiene` run's log and its `ledger-<owner>` artifact, as `GH_READ_TOKEN`, at 12:09 to 12:20 PT. Every pass listed reads `hygiene: event=schedule dry_run=false`.

## Identities

- **GitHub, observed — `GH_READ_TOKEN`:** the step summary reads `proof identity: github read — app agent-bureau-bot-2, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. Every GitHub read above was made with it as `GH_TOKEN=$GH_READ_TOKEN gh …`: both `proof_release.py check` calls, the compares, the tags, the deployment record, the pull requests and their timelines, and every Hygiene, Promote Channel and Merge Gate log and ledger artifact.
- **The refused write, 2026-10-09 12:18:06 PT:** `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-37977855483 -f sha=694eabc2…` answered `{"message":"Resource not accessible by integration",…,"status":"403"}`. A read of that ref then answered `404`, so nothing was created and nothing needed deleting.
- **GitHub, written — `GH_TOKEN`:** used only to push `agent/DRE-6274-proof-record` and to open this record's pull request. It was used for no observation.
- **Linear — `LINEAR_API_KEY`, the fleet key:** used for the reads above (the closed-card count, the comment searches, four cards' threads and histories, three receipts, this card's thread and its parent) and for this card's heartbeats and the `🤖 agent-actor` marker. Nothing was moved or edited, and no other card was commented on. **linear requests: 28 of 40.** That counts 20 for reads and earlier heartbeats, then the `⏳ 3/5`, `⏳ 4/5` and `⏳ 5/5` heartbeats and the actor marker at 2 each. The heartbeats and the marker were posted after this record was written.
- **aws: none.** The step summary reads `proof identity: aws — none, PROOF_ROLE_ARN not provided`. The two database rows are the operator's, quoted from this card.

## 1. The release lines

Read as `GH_READ_TOKEN`, 2026-10-09 12:07:30 PT:

    $ python3 scripts/proof_release.py check --repo dreadnought-foundry/bureau-pipeline --merge DRE-6173:810:3af8f8b5… (thirteen --merge flags)
    proof-release: ready — pipeline-channel (the whole repository): stable carries #810 (3af8f8b) for DRE-6173
    proof-release: ready — pipeline-channel (the whole repository): stable carries #814 (5829fdc) for DRE-6177
    proof-release: ready — pipeline-channel (the whole repository): stable carries #813 (8ede081) for DRE-6178
    proof-release: ready — pipeline-channel (the whole repository): stable carries #811 (df662e4) for DRE-6179
    proof-release: ready — pipeline-channel (the whole repository): stable carries #815 (cafb266) for DRE-6180
    proof-release: ready — pipeline-channel (the whole repository): stable carries #827 (8e37315) for DRE-6181
    proof-release: ready — pipeline-channel (the whole repository): stable carries #823 (64d11b9) for DRE-6182
    proof-release: ready — pipeline-channel (the whole repository): stable carries #821 (abbddbd) for DRE-6186
    proof-release: ready — pipeline-channel (the whole repository): stable carries #808 (d681dcb) for DRE-6189
    proof-release: ready — pipeline-channel (the whole repository): stable carries #812 (ae607cf) for DRE-6190
    proof-release: ready — pipeline-channel (the whole repository): stable carries #825 (874b112) for DRE-6247
    proof-release: ready — pipeline-channel (the whole repository): stable carries #809 (d81ac26) for DRE-6248
    proof-release: ready — pipeline-channel (the whole repository): stable carries #819 (afb7c10) for DRE-6273

    $ python3 scripts/proof_release.py check --repo dreadnought-foundry/agent-bureau --merge DRE-6176:3407:4a0bcc1e… --merge DRE-6198:3408:f34b67f4…
    proof-release: ready — console: newest agent-bureau-console-v1.6.364 carries #3407 (4a0bcc1) for DRE-6176
    proof-release: ready — console: newest agent-bureau-console-v1.6.364 carries #3408 (f34b67f) for DRE-6198
    proof-release: ready — website: untouched — #3407 (4a0bcc1) for DRE-6176, #3408 (f34b67f) for DRE-6198 changed nothing under website/ that it does not ignore
    proof-release: ready — relay: untouched — #3407 (4a0bcc1) for DRE-6176, #3408 (f34b67f) for DRE-6198 changed nothing under cloud/relay/ that it does not ignore
    proof-release: ready — relay-gh: untouched — #3407 (4a0bcc1) for DRE-6176, #3408 (f34b67f) for DRE-6198 changed nothing under cloud/relay-gh/ that it does not ignore

Each card's pull request was read by its `agent/DRE-<n>-…` branch: one merged pull request per card, and the merge commits were taken from them. The bureau-pipeline merges, all on 2026-10-08 PT, were:

- DRE-6248 #809 at 11:54;
- DRE-6189 #808 at 12:00;
- DRE-6173 #810 at 12:34;
- DRE-6190 #812 at 13:34;
- DRE-6178 #813 at 14:19;
- DRE-6177 #814 at 14:24;
- DRE-6180 #815 at 14:27;
- DRE-6273 #819 at 15:10;
- DRE-6186 #821 at 15:38;
- DRE-6179 #811 at 15:51;
- DRE-6182 #823 at 16:52;
- DRE-6247 #825 at 17:31;
- DRE-6181 #827 at 18:52.

agent-bureau's DRE-6176 #3407 merged at 13:41 PT and DRE-6198 #3408 at 13:47 PT.

## 2. The hygiene passes

The holds lane merged with DRE-6180 (#815, 14:27 PT on 2026-10-08). The first live scheduled pass to run it was the next one. Each row gives the run, its PT start, the commit it checked out, the ledger's time, and the lifts and carried rows on the dreadnought-foundry leg:

| Pass | Started (PT) | Commit | Ledger (PT) | Lifted | Carried |
|---|---|---|---|---|---|
| [37848804271](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37848804271) | 10-08 14:43:54 | `cafb266a` | 14:44:20 | 40 (+13 DeltaSolv, +5 EveryBite) | 494 |
| [37855318984](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37855318984) | 10-08 15:44:08 | `abbddbd8` | 15:44:26 | 40 | 454 |
| [37860985765](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37860985765) | 10-08 16:43:14 | `a9605232` | 16:43:38 | 40 | 414 |
| [37869258526](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37869258526) | 10-08 18:19:58 | `874b1125` | 18:20:21 | 40 | 374 |
| [37872917304](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37872917304) | 10-08 19:05:39 | `579f4f71` | 19:06:00 | 40 | 335 |
| [37876905221](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37876905221) | 10-08 19:55:58 | `411534a3` | 19:56:18 | 40 | 295 |
| [37881242573](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37881242573) | 10-08 20:52:16 | `55c0df2a` | 20:52:36 | 40 | 255 |
| [37885755793](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37885755793) | 10-08 21:50:34 | `55c0df2a` | 21:51:31 | 40 | 215 |
| [37890292085](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37890292085) | 10-08 22:47:55 | `870ff6fb` | 22:48:18 | 40 | 175 |
| [37896638220](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37896638220) | 10-09 00:01:07 | `94af70dd` | 00:01:33 | 40 | 135 |
| [37901482235](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37901482235) | 10-09 00:52:01 | `94af70dd` | 00:52:23 | 40 | 95 |
| [37907633661](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37907633661) | 10-09 01:52:13 | `94af70dd` | 01:52:35 | 40 | 55 |
| [37913670333](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37913670333) | 10-09 02:48:50 | `94af70dd` | 02:49:08 | 40 | 15 |
| [37919679888](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37919679888) — **finishing pass** | 10-09 03:46:45 | `8689eb67` | 03:47:05 | 15 | 0 |

Total through the finishing pass: 535 on the dreadnought-foundry leg, 13 on DeltaSolv and 5 on EveryBite, which is 553 cards. Those two legs lifted everything in the first pass and never carried a row. The first four passes ran before `stable` carried `8e37315`. Every later pass ran a commit that carries it, checked with `git merge-base --is-ancestor`. The pass at 19:05 PT carried one more row than the one before it (335, not 334): a card closed in between and joined the backlog.

After the finishing pass:

- passes 37925473543 (04:43 PT), 37933111373 (05:54), 37939403312 (06:47), 37946970561 (07:48), 37954121266 (08:45) and 37961614093 (09:47) lifted 0;
- [37968286272](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37968286272) (10:43 PT) lifted DRE-6404 (`because=new-head`, row 6) and DRE-6231 (`because=card-closed`);
- [37975942933](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37975942933) (11:49 PT) lifted DRE-6082 (`reason=review-cap-spent because=card-closed`).

The receipts on Linear: a comment search for `hyg-hold-cleared` between 2026-10-08 14:30 PT and 2026-10-09 04:30 PT, read at 12:18:27 PT, returned 521 receipts on 521 cards. It also returned 14 summary comments on DRE-5774. The 521 are every ledger lift except the 32 whose evidence says the card was archived. Linear's comment search does not return comments on archived issues, and DRE-3017's receipt was read directly instead (§3).

## 3. Three lifted cards and their receipts

Read off Linear, 2026-10-09 12:12:25 PT:

- **DRE-3017** (Done, archived at 2026-10-08 19:57:24 PT), receipt at 19:57:24 PT:
  `🧹 hygiene: hyg-hold-cleared — reason=manual because=card-closed · 19:56 PT` / `evidence: lane Done, no live stamp, archived — unarchived for this write and re-archived after it` / `📎 pipeline-act: hygiene-hold-clear · kind: recovery · state: unchanged · next: reconcile.py · discharges: nothing · subscriber: hygiene.yml · tag: hyg-hold-cleared`. It no longer carries `needs-human`.
- **DRE-4048** (Canceled), receipt at 2026-10-08 22:49:05 PT:
  `🧹 hygiene: hyg-hold-cleared — reason=manual because=card-closed · 22:48 PT` / `evidence: lane Canceled, no live stamp`. It no longer carries `needs-human`.
- **DRE-6194** (Canceled), receipt at 2026-10-09 03:48:02 PT, in the finishing pass:
  `🧹 hygiene: hyg-hold-cleared — reason=manual because=card-closed · 03:47 PT` / `evidence: lane Canceled, no live stamp`. It no longer carries `needs-human`.

## 4. The Green Light question on DRE-6353

The comment posted 2026-10-09 08:26:44 PT, one second after the stamp, is quoted whole here (the run checked this text):

    🚨 review-nudge-cap PR #834 @9950f171bd3c75d5ea2d508300960817d22db6ee:

    🔎 Finding: The merge-gate re-trigger budget is spent on head 9950f17: the sweep spent 3 re-triggers over 6.6h on the merge gate, and the gate declined to merge on those re-triggers. Standing on it: critic REQUEST_CHANGES, verifier none. The sweep has stopped re-triggering, because 3 windows on the same head is a stall, not a slow round.
    ❓ Question: Does the finding standing on this head stand and get fixed, or is the change dropped, closing the pull request and canceling the card by hand?
    💡 Recommendation: Fix the finding — the finding stands on this head and reviewing the same head again would say it again — the card comes back only on a new head, so push a fix, or write an Operator decision comment on the pull request, which starts the fix agent once on your words, and the fix it pushes is that head.

    DRE-6353 waits in Green Light, and the sweep re-triggers nothing more on this head. It comes back to In Review on its own once a new head lands on the pull request — the fix you push, or the one the fix agent pushes on your Operator decision — and the decision alone moves nothing. Dropping the change means closing the pull request and canceling the card by hand: the cancel is what lifts the hold, and no comment on this card or the pull request does it.

    review-nudge-cap @9950f171bd3c75d5ea2d508300960817d22db6ee

The check, run 2026-10-09 12:19:56 PT on `694eabc2`:

    $ python3 scripts/console_escalation.py check /tmp/lin/dre6353-question.txt
    exit 0
    $ python3 scripts/console_escalation.py check /tmp/lin/dre6082-question.txt
    exit 0
    --- the same text with the line marks stripped, as a control
    the Finding line (🔎 Finding:) is missing
    the Question line (❓ Question:) is missing
    the Recommendation line (💡 Recommendation:) is missing
    exit 1

The question appeared twice, on DRE-6353 and DRE-6082, and each time the review-cap hold placed it in Green Light rather than leaving the card in In Review with a bare label.

## 5. The `tried_first` table

From `config/holds.json` at `694eabc2`, one row per reason. `python3 scripts/hold.py check` printed `14 hold writer site(s), 14 row(s), 0 problem(s)` at 12:12:47 PT.

| Reason | Lifts by | Writer site(s) | The step it tries first | Receipt that records it |
|---|---|---|---|---|
| `stranded-no-run` | `run-started` | `scripts/reconcile.py` | The sweep re-sends a card that sat in Todo with no run, once, before it stamps the strand (DRE-5743). A second unattended re-send is the move that already failed. | `card sat in Todo with no run — re-dispatched` |
| `no-route` | `repo-on-rail` | `scripts/reconcile.py` | The card is moved to the operator's queue under the card-stranded act; the move lands with DRE-6177, and the row names it now. | `card-stranded` |
| `review-cap-spent` | `new-head` | `scripts/reconcile.py` | Three review re-triggers on the head (REVIEW_NUDGE_CAP), then the evidence-built Green Light question, which lands with DRE-6181. | `review-nudge-cap` |
| `dead-run-cap` | `unpark-marker` | `scripts/reconcile.py` (the sweep); `scripts/dead_run.py` (the build run's park) | Two requeues at the same budget (REQUEUE_CAP); then one hand-off to the planner, decided by dead_run.decide (DRE-6178, DRE-6186), on a silent death — no pull request and no blocker note. A credential refusal or an API or model death at the cap holds as today, because neither is a size reading. | `✂️ dead-run-cap → Planning:` |
| `turn-cap-park` | `unpark-marker` | `scripts/dead_run.py` | One requeue at the same budget after a turn-cap death past implementation green (DRE-4366). | `turn-exhaustion-requeue` |
| `epic-rereview-twice` | `manual` | `scripts/rereview_watch.py` | The watcher's own first firing asks for the second critic's review again. | `asked for the review again` |
| `plan-critic-bound` | `manual` | `.github/workflows/plan.yml` (6 sites) | The pipeline's own second chance before each park: a revision round after a send-back, a re-run at a higher turn ceiling after a turn-cap death, a second ask after no result. Each round is recorded as `plan-critic: stage=<stage> round=<n>`, composed in plan_critic.py, beside the 🔁 notices. | `{MARKER_PREFIX} stage={stage} round=` |
| `fix-dispute` | `new-head` | `.github/workflows/agent-fix.yml` | The fix loop's rounds on the pull request, up to its budget. | `Fix budget exhausted` |
| `unfixable-check` | `new-head` | `.github/workflows/agent-fix.yml` | The fix run itself: it read the red check on this head and found nothing a fix could change. | `unfixable-check-hold @` |
| `manual` | `manual` | `scripts/model_adoption_actions.py` | none — a person, or a writer creating a card already held, chose it | — |

The two `dead-run-cap` sites word their step separately in the registry. The sweep's adds: "What the sweep sees at the cap is always the silent death … so the hold is the second strike on the budget". The build run's adds: "which lands with DRE-6178 and the sweep hand-off DRE-6186". Both name the same receipt.

## 6. The dry runs

Both ran on `694eabc2`, the released commit, in this run's checkout. pytest came from `requirements-dev.txt` (`pytest==9.1.1`).

The Triage lane over `tests/fixtures/hygiene-triage-2026-09-30.json`, 12:13:48 PT. This driver was run from the repository root and is not committed:

    import os, sys, json
    sys.path.insert(0, "tests"); sys.path.insert(0, "scripts")
    os.environ["HYGIENE_DRY_RUN"] = "1"
    import test_hygiene_triage as T
    import hygiene
    doc = T.fixture()
    gh = T.FixtureGh(doc); lin = T.FixtureLinear()
    ctx = hygiene.make_context(T.HOME, gh=gh, linear=lin, dry_run=os.environ["HYGIENE_DRY_RUN"] == "1",
                               now=T.NOW, summary_card=T.SUMMARY)
    hygiene.discover = lambda lane_dir=None: [T.lane]
    def refuse(write, ctx): raise AssertionError(f"a dry run sent {write.describe()}")
    hygiene.send = refuse
    board = {"lanes": {n: [c for c in cs if c.get("identifier") != T.SUMMARY] for n, cs in doc["lanes"].items()}, "prs": doc["prs"]}
    ledger = hygiene.run_leg(board, ctx)
    ...

Its output:

    outcomes: ['would']
    Left row: {"lane": "Triage", "target": "DRE-4423", "why": "held no-route on repo:legacy-site — the slug is not on the dispatch rail", "recommendation": "correct the card's repo: label to a slug on the rail, or add the repo to config/repo-map.json if it should route; the holds lane reads the card's current label, lifts the hold and sends the card to Planning on its next pass, and nothing on the card needs clearing"}
    actions on DRE-4423: []

    $ python3 -m pytest tests/test_hygiene_triage.py -q -k NoRoute
    10 passed, 43 deselected in 0.29s

The dead-run cap, 12:14:06 PT. The thread file was this JSON array of bodies, the shape `linear_ops.py dump-comments` prints:

    ["⏳ 1/5 plan", "🪦 dead-run-requeue: agent died with no PR and no blocker note — requeued to Todo for a fresh attempt (dead run 1/3).", "⏳ 1/5 plan", "🪦 dead-run-requeue: agent died with no PR and no blocker note — requeued to Todo for a fresh attempt (dead run 2/3)."]

    $ python3 scripts/dead_run.py decide 2 --comments-file /tmp/lin/thread-no-mark.json
    replan

    ✂️ dead-run-cap → Planning: the dead-run cap is reached — 3 runs on this card's budget ended with no pull request, the last with no blocker note either (dead run 3/3). That is the one death that can mean the card is too big for a run, so the planner gets the card once before a person is asked to look.
    …
    The planner reads this and either splits the card on its file footprint or sends it back as one piece. Nothing is parked and no label is written; if the card comes back as one piece and its next run dies the same way, the cap parks it for a person.
    exit 0

    $ python3 scripts/dead_run.py decide 2 --comments-file /tmp/lin/thread-no-mark.json --credential-expiry
    hold

    🚨 held-for-human (dead-run-requeue cap reached): the run's GitHub credential was refused before the branch could reach GitHub — the agent finished the work and could not deliver it. …
    exit 0

    $ python3 -m pytest tests/test_dead_run_split_first.py tests/test_hold_sweep_dead_run.py -q
    104 passed in 183.52s (0:03:03)

A first try passed the thread as `{"body", "createdAt"}` records instead. `decide` printed `dead_run: could not read the card's thread … — the cap holds as it always has` and answered `hold`, which is its documented fallback for an unreadable thread. The run above uses the shape `dump-comments` prints.

## 7. The merge gate over the window

Runs created between 2026-10-08 19:01:18 PT and 2026-10-09 12:05:45 PT, listed and downloaded as `GH_READ_TOKEN`:

| Repository | Workflow | Runs | Logs read | `decision=` lines | Lines mentioning `needs-human` |
|---|---|---|---|---|---|
| bureau-pipeline | Self Merge Gate | 168 (78 skipped) | 90 | 90: merge 17, wait 71, hold 2 | 0 |
| agent-bureau | Merge Gate | 370 (133 skipped, 6 cancelled, no log) | 231 | 224: merge 28, wait 192, hold 1, conflict 3 | 0 |
| portico | Merge Gate | 22 (11 skipped) | 11 | 11: merge 3, wait 7, hold 1 | 0 |
| agent-bureau-demo | Merge Gate | 0 | 0 | 0 | 0 |

Across the three repositories that ran, the 325 decisions were 48 merge, 270 wait, 4 hold and 3 conflict. One decision as the log prints it (bureau-pipeline run 37872867656, 2026-10-08 19:05 PT): `decision=merge` / `reason=CI green + critic APPROVE bound to f3c0fbe2… — merge as qa-bot` / `merged PR #826`. No `decision=` or `reason=` line in any of the 332 logs contains the word `label`.
