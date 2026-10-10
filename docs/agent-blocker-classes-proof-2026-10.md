# PROOF record — DRE-6510: a blocker's class is read off the real board and one live sweep names every open blocker by its class — classifier dry run, board dry run, live sweep line (epic DRE-6413)

**Status: PASS.**

The classifier read the three evidence cards on the live board and printed the class each card's recorded blocker carries: `wrong-repo` for DRE-3242, `branch-without-pr` for DRE-6056 and `nothing-to-change` for DRE-5195. The board dry run, started 1 minute 41 seconds after the newest full Reconcile pass completed, printed no line — the bureau-pipeline Backlog held no open agent blocker this morning. That pass ran on a `main` that carried every card of the epic, and its log holds no line of the old skip that held a blocked card until a person replied.

Two facts the epic's text reads otherwise. No live cancel is shown by this proof: a cancel needs a note written after DRE-6443 and DRE-6444 are live, with every criterion attested under the stamp, and no note on the board is one. DRE-5195's legacy `nothing-to-change` note attests nothing, so it is sent to Planning, never canceled. And there is no separate read-only Linear user — the reads ran on the fleet key, which the `linear-budget:` lines below name as `budget: fleet`. What kept them harmless is that the commands write nothing.

## How this was recorded

- **Run:** proof run [38065218145](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38065218145), attempt 1, dispatched for the first proof run of this card.
- **Commit observed:** `main` at `9c5b7d24768bc94b4310380b7410877359eabc4c` (the merge of #937, 2026-10-10 07:25:52 PT). The CLI commands ran from this checkout, and the Reconcile pass read below ran the pipeline at the same commit (`pipeline_ref: main`, checked out as `9c5b7d24…`). The repo's own sweep runs the pipeline at `main`, not at a tag. The newest tag, `v5` (`33e0be86`, 2026-07-21), predates the epic and is not what the sweep runs.
- **The epic's merges, all ancestors of `9c5b7d24`** (`git merge-base --is-ancestor`, each confirmed):

  | Card | Piece | Merge | Merged (PT) |
  | -- | -- | -- | -- |
  | DRE-6438 | blocker classes vocabulary and reader | #885 `7c649b08` | 2026-10-09 18:44:24 |
  | DRE-6446 | action module: `wrong-repo` | #891 `f9462eea` | 2026-10-09 19:33:36 |
  | DRE-6458 | action module: `nothing-to-change` | #890 `0129b19b` | 2026-10-09 20:13:05 |
  | DRE-6443 | the build brief's class stamp | #888 `9218d695` | 2026-10-09 22:19:52 |
  | DRE-6444 | the class poster | #894 `50cedd5d` | 2026-10-09 22:22:35 |
  | DRE-6508 | the resolver | #915 `fa1d94e7` | 2026-10-10 00:01:55 |
  | DRE-6447 | action module: `branch-without-pr` | #903 `c8be99c9` | 2026-10-10 00:23:01 |
  | DRE-6448 | the gate | #920 `5a7bafc9` | 2026-10-10 00:47:21 |
  | DRE-6459 | action module: the Green Light ask | #923 `d0e9cce2` | 2026-10-10 01:26:16 |
  | DRE-6509 | the classes through the sweep | #928 `dc2bb754` | 2026-10-10 02:39:10 |

  The newest of them, DRE-6509, merged at 02:39:10 PT, six hours before the pass read below started.
- **Times:** every time here is Pacific. The run's first read was the `⏳ 1/5 read` heartbeat at 2026-10-10 08:51 PT.
- **Local run:** none declared (`PROOF_LOCAL_STATUS=none`). The card has no `Local screen:` row, so none is needed.

## Identities

- **GitHub, observed — `GH_READ_TOKEN`.** `proof identity: github read — app agent-bureau-bot-3, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. Used for `gh run list` and `gh run view --log` on the Reconcile workflow, the pull request body of #928, and the repo's tags and releases.
- **The refused write**, at 2026-10-10 08:52:30 PT:

      GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs \
        -f ref=refs/heads/proof-write-probe-38065218145 -f sha=9c5b7d24768bc94b4310380b7410877359eabc4c

  GitHub's answer: `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}` — `gh: Resource not accessible by integration (HTTP 403)`. No ref was created.
- **GitHub, written — `GH_TOKEN`.** Used only to push `agent/DRE-6510-proof-record` and open its pull request.
- **Linear — `LINEAR_API_KEY`, the fleet key**, not the planner OAuth token: no line reads `budget: planner-oauth`. Every invocation's `linear-budget:` line names it, for example `linear-budget: 2435 → 2433 (spent 2 this run; window resets 09:52 PT; limit 2500; budget: fleet)`. Used for the three `classify-card` reads, the `board` read, one read of this card's parent epic, the heartbeats and the actor marker. It wrote nothing but those heartbeats and that marker on DRE-6510, and nothing on any other card.
- **linear requests: 16 of 40** when this record was pushed — `⏳ 1/5` 2, `⏳ 2/5` 2, `classify-card` 1 + 1 + 1, `board` 4, the parent-epic read 1, `⏳ 3/5` 2, `⏳ 4/5` 2, summed from each invocation's `linear-calls:` line, each `process:` token once. Two writes are owed after the push and are not in that number: `⏳ 5/5 PR opened` and the actor marker. The cap was never near.
- **aws: none** — `proof identity: aws — none, PROOF_ROLE_ARN not provided`. No row needs it.
- **Scratch state:** none created.

## The criteria

| Criterion | Result |
| -- | -- |
| Dry run, the evidence cards: `python3 scripts/blocker_class.py classify-card DRE-3242`, `… DRE-6056` and `… DRE-5195`, run against the live Linear board on the key `proof-task.yml` hands the run as `LINEAR_API_KEY` (the fleet key, or the planner OAuth token under `LINEAR_AGENT_BUCKET=planner` — the run's `linear-budget:` line says which, and is quoted), print `wrong-repo`, `branch-without-pr` and `nothing-to-change` respectively, read off each card's newest recorded blocker comment over the WHOLE thread, not the newest fifty comments (thirteen comments sat after DRE-3242's and DRE-6056's markers on 2026-10-09, and both threads keep growing); the three output lines are quoted in the record with the time read. | Met. At 2026-10-10 08:51:29 PT the three reads printed `DRE-3242 class=wrong-repo`, `DRE-6056 class=branch-without-pr` and `DRE-5195 class=nothing-to-change`, each exiting 0. Each ran on the fleet key, quoted as `budget: fleet` from its `linear-budget:` line. The command reads the thread with `linear_ops.comment_bodies(card, whole_thread=True)` (`scripts/blocker_class.py` line 290), which pages past the fifty-comment window whenever Linear reports an older page. See "Row 1" below. |
| Dry run, today's board: `REPO=dreadnought-foundry/bureau-pipeline REPO_SLUG=bureau-pipeline python3 scripts/blocker_class.py board` over the live Backlog, on the same key, prints one `<card> class=<class>` line for every open agent blocker, open as the sweep reads it (`reconcile.has_unresolved_blocker`) — zero lines on a board with none — and exits 0 with nothing written (the command has no write path, DRE-6438's test pins zero mutations, and the record quotes the `linear-calls:` line the invocation printed); the full output and the count are quoted, and the record names which lines carry a mechanical class and which are `question`. | Met. At 2026-10-10 08:52:02 PT, 1 minute 41 seconds after the pass below completed, the command printed zero lines on standard output and exited 0 at 08:52:04 PT. Count: 0 — no open agent blocker on the bureau-pipeline Backlog, so no line carries a mechanical class and none is `question`. It printed `linear-calls: 4 request(s) this run (process: 3121@claude-deaca5; budget: fleet)`. Nothing was written: `git status --porcelain` was empty afterwards, the command has no write path, and DRE-6438's `tests/test_blocker_class.py::test_it_prints_one_line_per_open_card_and_writes_nothing` pins zero mutations (`assert writes.mutations == []`). See "Row 2" below. |
| One live FULL Reconcile pass of `bureau-pipeline` — a scheduled run (`self-reconcile.yml`'s `*/15 * * * *` cron) whose log carries the `promotion: WIP cap` line and neither a `promote-only:`, a `close-only:` nor an `idle:` line, since those passes print no per-card blocker resolution — that STARTED after the gate card (DRE-6448), the resolver card (DRE-6508) and all four action-module cards were on `main`, and COMPLETED before the `board` dry run above started. A proof run reads and cannot cause a sweep, so the row is anchored on a pass that has already run: the run reads the pass FIRST — the newest completed such run, found with `gh run list` on the Reconcile workflow, identified by its run id and start time in PT — and runs the `board` dry run immediately after it, so the two reads are minutes apart, and the record quotes the pass's run id and start time and the dry run's time. When the run starts, such a pass has normally already run — the sweep fires every fifteen minutes and is what dispatched this proof — and when none has (the last of those merges is newer than every completed full pass), the run waits for the next scheduled pass to complete, at most fifteen minutes plus that pass's own running time, then reads it and says so in the record. If no full pass completes within forty-five minutes of the run's first read, the row reads `Not observed. waiting for a scheduled full Reconcile pass: none completed in 45 minutes — <the newest run seen and its state>`, which holds the record's merge gate under DRE-6141, and that hold is right: three quarters of an hour with no sweep is a pipeline outage the record must not paper over. Its log carries NO line reading `has an unresolved agent-blocker — skipping`; and for every card the dry run printed, the log carries exactly ONE `promotion:` line naming that card, which is either a line in the class-naming grammar (opening `promotion: <card> agent-blocker class=<class>` — `resolved —`, `— not resolved this pass:`, `— action module … is not on this checkout`, `— no longer in Backlog since the board was read` or `— resolved since the board was read`) or, for a card a gate ahead of the resolver held on that pass, that earlier gate's own line for the card — the gates DRE-6448 places ahead of the resolver, all of them outside the read guard: the epic-is-an-epic skip, the hold skip, the inactive-epic skip, the unread-relations skip and the undeclared-prose-blocker move, each printing its own `promotion: <card> …` line, and a card held there never reaches the resolver by design. The formal-blockedBy hold, the epic gate (`epic_blockers_unmet`) and the verdict refusals sit BEHIND the resolver (DRE-6448, "Where the block sits"), so a card held by one of those and carrying an open marker gets the resolver's class-naming line on the pass that reads its marker, and that gate's line only on a later pass; the record reads the line it finds and says which gate wrote it. The record quotes each card's line and says which of the two it is; a card whose only line is an earlier gate's is `Met.`, because the sweep named why it stood, which is the epic's rule. Two skews between the pass and the dry run are named in the record and never counted against the row: a card the dry run printed whose open marker is NEWER than the pass's start time, which the pass could not have read, named with the marker's time; and a card the pass's log names in the class grammar that the dry run did not print — its marker answered or resolved, or the card out of Backlog, between the two reads — named with the comment or the lane move and its time. A card for which that pass printed the stderr line `ERROR: <card> agent-blocker class=` instead of a `promotion:` line is quoted, and the row reads `Not met.`: a module failed on it. With zero cards printed, the row is met on the absent legacy line alone, and says the board held no open blocker at the time (on 2026-10-09 the bureau-pipeline Backlog held none). | Met. The pass is scheduled run [38065104459](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38065104459), `event: schedule`, `conclusion: success`, started 2026-10-10 08:48:34 PT and completed 08:50:21 PT, on `main` `9c5b7d24`. It started 7 hours 22 minutes after the newest of the gate, the resolver and the four action modules reached `main` (DRE-6459, 01:26:16 PT), and completed before the dry run started at 08:52:02 PT. It was read first, at 08:51:51 PT. Its log holds `promotion: WIP cap 8, read from this run's max_wip input` once, and no `promote-only:`, `close-only:` or `idle:` line. It holds no `has an unresolved agent-blocker` line, no `agent-blocker class=` line and no `ERROR: … agent-blocker class=` line. The dry run printed zero cards, so the row is met on the absent legacy line alone: the bureau-pipeline Backlog held no open agent blocker at the time. No skew to name. See "Row 3" below. |
| The record merges on `agent/DRE-<n>-proof-record` with the critic's APPROVE at the merged head; the CEO closes this card after reading the merged record. | Open: this pull request's own merge, and then the CEO's step. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## Row 1 — the classifier on the three evidence cards

Command, run from the checkout of `main` at `9c5b7d24`, at 2026-10-10 08:51:29 PT:

    for c in DRE-3242 DRE-6056 DRE-5195; do python3 scripts/blocker_class.py classify-card $c; done

Verbatim output, standard output and standard error together:

    DRE-3242 class=wrong-repo
    linear-budget: 2439 → 2439 (spent 0 this run; window resets 09:51 PT; limit 2500; budget: fleet)
    linear-calls: 1 request(s) this run (process: 3033@claude-f5ab62; budget: fleet)
    DRE-6056 class=branch-without-pr
    linear-budget: 2438 → 2438 (spent 0 this run; window resets 09:51 PT; limit 2500; budget: fleet)
    linear-calls: 1 request(s) this run (process: 3035@claude-39cc5f; budget: fleet)
    DRE-5195 class=nothing-to-change
    linear-budget: 2438 → 2438 (spent 0 this run; window resets 09:51 PT; limit 2500; budget: fleet)
    linear-calls: 1 request(s) this run (process: 3037@claude-13d927; budget: fleet)

Each exited 0. On the whole thread: `classify-card` reads `linear_ops.comment_bodies(card, whole_thread=True)` and takes the newest marker in it (`newest_marker`). `whole_thread` makes `_fetch_thread` walk older pages for as long as Linear's `pageInfo.hasNextPage` says one exists (`scripts/linear_ops.py` lines 3763–3780). Each read cost one request, so Linear reported no page older than the first one for any of the three threads. These reads are the whole thread as Linear holds it, and they printed the three expected classes.

## Row 2 — the board dry run

The pass in row 3 was read first, at 08:51:51 PT. Then, at 2026-10-10 08:52:02 PT:

    REPO=dreadnought-foundry/bureau-pipeline REPO_SLUG=bureau-pipeline python3 scripts/blocker_class.py board

Standard output, verbatim: nothing, zero lines.

Standard error, verbatim:

    linear-budget: 2435 → 2433 (spent 2 this run; window resets 09:52 PT; limit 2500; budget: fleet)
    linear-calls: 4 request(s) this run (process: 3121@claude-deaca5; budget: fleet)

It exited 0 at 08:52:04 PT. `git status --porcelain` afterwards printed nothing. The command walks `reconcile.backlog_children(from_linear=True)`, keeps cards whose `repo:` is `bureau-pipeline`, and prints a line only for a card `reconcile.has_unresolved_blocker` calls open (`scripts/blocker_class.py` lines 295–315). It has no write call. Count: 0 lines — no mechanical class and no `question`.

## Row 3 — the live Reconcile pass

Read at 2026-10-10 08:51:51 PT, as the read token:

    GH_TOKEN=$GH_READ_TOKEN gh run list -R dreadnought-foundry/bureau-pipeline --workflow self-reconcile.yml --limit 12 \
      --json databaseId,event,status,conclusion,createdAt,startedAt,updatedAt,headSha,displayTitle

The newest completed run with `event: schedule`, verbatim:

    {"conclusion":"success","createdAt":"2026-10-10T15:48:34Z","databaseId":38065104459,"displayTitle":"Reconcile","event":"schedule","headSha":"9c5b7d24768bc94b4310380b7410877359eabc4c","startedAt":"2026-10-10T15:48:34Z","status":"completed","updatedAt":"2026-10-10T15:50:21Z"}

That is 08:48:34 PT to 08:50:21 PT. Its log was read with `GH_TOKEN=$GH_READ_TOKEN gh run view 38065104459 -R dreadnought-foundry/bureau-pipeline --log`, 783 lines.

What the pass ran: `Uses: dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@refs/heads/main (9c5b7d24768bc94b4310380b7410877359eabc4c)`, with `pipeline_ref: main`, and `.bureau-pipeline` checked out at `9c5b7d24768bc94b4310380b7410877359eabc4c`. The sweep step runs `python3 .bureau-pipeline/scripts/reconcile.py`.

Line counts in the log:

| Pattern | Lines |
| -- | -- |
| `promotion: WIP cap` | 1 |
| `promote-only:` | 0 |
| `close-only:` | 0 |
| `idle:` | 0 |
| `has an unresolved agent-blocker` | 0 |
| `agent-blocker class=` | 0 |

The one `ERROR:` string in the log is the workflow's own script source echoed at 08:48:47 PT (`LOST=$(grep -E '^(ERROR: )?runner-lost:' sweep.log || true)`), not a line the sweep printed.

The promotion block, verbatim (timestamps in UTC as Actions prints them, 15:49 UTC is 08:49 PT):

    promotion: WIP cap 8, read from this run's max_wip input
    promotion: DRE-2611's epic DRE-3711 is not active (Intake) — skipping
    promotion: DRE-2423's epic DRE-3711 is not active (Intake) — skipping
    promotion: DRE-2658's epic DRE-3711 is not active (Intake) — skipping
    promotion: DRE-3710's epic DRE-3711 is not active (Intake) — skipping
    promotion: DRE-3871's epic DRE-3711 is not active (Intake) — skipping
    promotion: DRE-3873's epic DRE-3711 is not active (Intake) — skipping
    promotion: DRE-4203's epic DRE-3711 is not active (Intake) — skipping
    promotion: DRE-4205's epic DRE-3711 is not active (Intake) — skipping
    promotion: DRE-5234 is an epic — epics are promoted by humans, never by the sweep; skipping
    promotion: DRE-5235's epic DRE-5234 is not active (Backlog) — skipping
    promotion: DRE-6044 is an epic — epics are promoted by humans, never by the sweep; skipping
    promotion: DRE-6045's epic DRE-6044 is not active (Backlog) — skipping
    promotion: DRE-6466 is held by DRE-6510 (Hand-work), declared by a formal blockedBy relation — skipping
    promotion: DRE-6605's epic DRE-6603 is not active (Triage) — skipping
    promotion: DRE-6606's epic DRE-6603 is not active (Triage) — skipping
    promotion: DRE-6607's epic DRE-6603 is not active (Triage) — skipping
    promotion: DRE-6608's epic DRE-6603 is not active (Triage) — skipping
    promotion: DRE-6609's epic DRE-6603 is not active (Triage) — skipping
    promotion: DRE-6610's epic DRE-6603 is not active (Triage) — skipping
    promotion: DRE-6612's epic DRE-6603 is not active (Triage) — skipping
    promotion: DRE-6613's epic DRE-6603 is not active (Triage) — skipping
    promotion: DRE-6614's epic DRE-6603 is not active (Triage) — skipping
    promotion: 0 card(s) promoted, 0 parentless one-off(s), 0 hand-built (nothing dispatched) (WIP 0+0/8)

No card in that block is named under the old blocker skip or under a blocker class. That matches the dry run's zero lines a minute and 41 seconds after the pass completed. The legacy line no longer exists in the sweep's source either: `scripts/reconcile.py` at `9c5b7d24` prints the class-naming head `promotion: {ident} agent-blocker class={blocker.cls}` (line 7451) and holds no `has an unresolved agent-blocker — skipping` string.

What this row does not show, and why: with no open blocker on the board, the pass named no card by its class. The first live classed blocker and the sweep's first live resolution are DRE-6466, the `operator-step` card this one blocks — the promotion block above shows the sweep holding it on that relation.
