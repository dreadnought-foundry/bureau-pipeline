# PROOF record — DRE-6678: one morning's groom proposal keeps Not ready cards off the list, quotes an undecided question on it, rewrites partly solved cards first, and leaves an unverified card out with its reason (epic DRE-6644)

**Status: PASS after two rewords.** On 2026-10-20 the operator session reworded the Rewrite first row and split the last row on the card, under the CEO's standing answer for rows that cannot be met as worded. Both now read Met. from this record's own readings; the old wording is on the card.

Both mornings have run. The release carrying DRE-6664 through DRE-6667 went live on 2026-10-10 at
18:31 PT. The first scheduled morning, run 38141699804 on `main` at `480b5cf5`, posted proposal
`ab09ece4ddee` on DRE-4541 at 06:13 PT on 2026-10-11. That proposal was never approved, so no drain
followed it. The second, run 38255458434 on `main` at `5d71a589`, posted proposal `06e997f2ba4c` at
06:32 PT on 2026-10-12. The CEO approved it in the console at 08:08 PT, and the drain ran at 08:08 PT.
Of the nine judged rows, rows 2, 3, 4 and 6 are met. Rows 2 and 4 are read on the second morning and its
drain, because the first morning had no drain. Row 1 is accepted by operator decision: it held as worded
on the second morning, not the first. Row 7 is accepted by operator decision: on both mornings the rule did not hold a card
whose read names an epic with only its proof card open, and the CEO then decided that such an epic
counts as built out for this hold, while the proof still runs and closes. Rows 5 and 8 wait on events that neither
morning held: a Planning unit marked in flight, and the CEO's acceptance of a Rewrite first draft.
Row 9 waits on the merge. The operator decisions are https://github.com/dreadnought-foundry/bureau-pipeline/pull/981#issuecomment-6133780913
(row 1) and https://github.com/dreadnought-foundry/bureau-pipeline/pull/981#issuecomment-6138024420 (row 7),
each written by a session that took none of these readings.

The critic held this record at 19:03 PT on 2026-10-10 because no morning had
yet run on the new code, and a second proof run was dispatched at 19:22 PT,
before the morning. It found the
same board: no `self-groomer.yml` run after 10:05 PT, and no proposal on the
standing card newer than `a4f198411b02`. Nothing changed in the rows below
except the run ids.

The critic held it again at 19:26 PT for the same reason, and a third proof
run was dispatched at 19:55 PT, still before the morning. It found the same
board again. The rows then waited on the morning of 2026-10-11, and a run
dispatched before it could add only its own run id.

## How this was recorded

- **2026-10-12, read by hand.** No proof run was dispatched for the second morning. The full-stack
  engineer session read it between 15:00 PT and 15:25 PT on 2026-10-12 and amended rows 1 to 9. It
  changed nothing on the board.
  - **GitHub, as `agent-bureau-bot`** (a token from agent-bureau's `scripts/github-bot-token.sh`,
    15:02 PT): `gh run list --workflow self-groomer.yml`, `gh run view 38255458434` and the logs of
    its `groom` job (114832318494) and `post` job (114835446522), `gh run download 38255458434` for
    `groom-proposal`, `groom-proposal-verified` and 30 `groom-verdict-*` artifacts, and
    `gh api …/compare/67749a05…5d71a589` for DRE-6910's merge.
  - **Our database, through agent-bureau's `make db-read`** (read-only; 15:06 PT, 15:11 PT, 15:12 PT,
    15:18 PT and 15:20 PT): the comments on DRE-4541 since the first morning, card states and state
    events, the children of the epics the rows name, a search of descriptions for
    `decide before building` and for each file a Not ready row names, the `groom_removal` rows, and every
    comment carrying `groom-added`, `groom-rewrite-accepted` or `groom-rewritten`. The card names
    `linear_ops.py` for states and descriptions. The database was read instead, as on 2026-10-11, for
    its state history between the two mornings. No Linear request was made.
  - **The runs after the first morning:** 38146370841 (`schedule`, 07:14 PT on 2026-10-11, the
    off-hour cron of the pair; it posted nothing), 38255458434 (`schedule`, 06:24 PT on 2026-10-12, the
    morning, `5d71a589`), 38262476066 (`schedule`, 07:22 PT, the off-hour cron; it posted nothing) and
    38268032518 (`repository_dispatch`, 08:08 PT, the drain of `06e997f2ba4c`, `12ac7585`).
  - **The CEO's decisions on the second proposal**, read from DRE-4541's thread in our database:
    `🧺 groom-excluded: 06e997f2ba4c DRE-7000 — move to Cancel` (08:08 PT),
    `🧺 groom-excluded: 06e997f2ba4c DRE-3589 — move to Cancel` (08:08 PT) and
    `🧺 groom-approved: 06e997f2ba4c` (08:08 PT), each `Decided in the console by` him with a signed
    console receipt. Then `🧺 groom-drained: 06e997f2ba4c` at 08:08 PT:
    `moved: 4 · held back: 12 · added: 0 · cancelled: 3 · refused: 32`.
- **2026-10-11, read by hand.** Both proof re-runs were spent on 2026-10-10, at 19:22 PT and
  19:55 PT, before the morning they waited for, so no run was coming. The operator session read
  the morning's page itself between 06:48 PT and 07:13 PT on 2026-10-11 and amended rows 1, 2,
  3, 4, 5, 7 and 9. Rows 6 and 8 are as the third run left them.
  - **GitHub, as `agent-bureau-bot`** (a token from agent-bureau's `scripts/github-bot-token.sh`,
    07:02 PT): `gh run list --workflow self-groomer.yml`, `gh run view 38141699804`, the logs of its
    `groom` job (114476736475) and `post` job (114478567652), and `gh run download 38141699804` for
    `groom-proposal`, `groom-proposal-verified` and six `groom-verdict-*` artifacts. A first
    download at 06:49 PT under a person's own `gh` sign-in gave the same bytes; the rows
    quote the bot's copy.
  - **Linear, through `scripts/linear_ops.py` at `stable` (`16527146`)** with the operator-tools
    key from Secrets Manager, which is neither a person's sign-in nor the fleet key:
    `dump-comments DRE-4541`, `description` for DRE-4072, DRE-3857, DRE-3603, DRE-3549 and DRE-3514,
    `dump-comments --with-authors` for the last three, and `groom_readiness.read_named` and
    `read_epics`, the readiness rules' own board read. That is 12 requests; one more, earlier, read
    this card. Under that key `authored_by_pipeline` reads false on every comment, so the drafts' author
    was read from our database.
  - **Our database, through agent-bureau's `make db-read`** (read-only; 06:54 PT, 07:00 PT,
    07:04 PT and 07:12 PT): each card's state, the time of each move, parents and children, and the search of
    every Intake description. The card names `linear_ops.py state-of` for states. The database was
    used for its breadth and for the times, and `read_named` gave the same states at 07:03 PT.
  - **The board moved while it was read.** DRE-6870 went Done at 06:23 PT, DRE-6899 to In Review
    at 06:24 PT, DRE-6902 Done at 06:44 PT, and DRE-6781 and DRE-6869 to In Progress at 06:59 PT
    and 07:00 PT. Each row is judged against the board as the run read it at 06:08 PT
    (`groomer: readiness read 37 cards in flight and 4 named cards`), and each later move is named
    in its row.
  - **No drain had run.** At 07:12 PT the newest comments on DRE-4541 in our database were the
    proposal and its `🧠 model-attempt` line, and `gh run list` showed no `repository_dispatch` run
    of `self-groomer.yml` after 10:05 PT on 2026-10-10.
- **The third run:** proof run
  [38106722129](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38106722129)
  (`Proof Task`, dispatched as the re-run after the critic's findings at `db55c91`), started
  2026-10-10 19:55 PT on `main` at `97f8f3ad904fef9ec0222130d5958323acd85329`. Observed
  2026-10-10 19:56–20:05 PT. It resumed this branch, merged `main` (`97f8f3ad`) into it, and
  re-read:
  - `gh run list --workflow self-groomer.yml` still lists 38070329055 (drain, 10:05 PT) as the
    newest run. No run has started since.
  - `stable` has moved to `c5295316a85f2ce9412eabc72c03afa07fd83072`, a commit dated 2026-10-10
    19:22 PT. `gh api repos/…/compare/<merge>...stable` and `…/compare/<merge>...main` answer
    `ahead` for each of the four merges (`3e09de8`, `8709bd1`, `f97f675`, `0b23ca7`).
  - `linear_ops.py dump-comments DRE-4541` returned 50 comments. They hold three proposals:
    `29a3110fec31` (drained 2026-10-08 11:23 PT), `467bdb8ee5f7` (drained 2026-10-09 09:06 PT) and
    `a4f198411b02` (drained 2026-10-10 10:05 PT). None carries `## Not ready`, `## Rewrite first`,
    a `Not verified — not in the batch` line or an `in flight — serves` mark.
  - The cron lines on `main` are still `0 13 * * *` and `0 14 * * *`.
  - It did not re-run `proof_release.py check`. The compares above stand in for it.
- **The second run:** proof run
  [38104939908](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38104939908)
  (`Proof Task`, dispatched as the re-run after the critic's findings at `3a1671b`), started
  2026-10-10 19:22 PT on `main` at `47073ee5670da6cab316c4814b1f433a9c291421`. Observed
  2026-10-10 19:23–19:35 PT. It resumed this branch, merged `main` (`c5295316`) into it, and
  re-read what the first run read:
  - `gh run list --workflow self-groomer.yml` still lists 38070329055 (drain, 10:05 PT) as the
    newest run. No run has started since.
  - `stable` has moved to `47073ee5670da6cab316c4814b1f433a9c291421`, a commit dated 2026-10-10
    19:10 PT. `proof_release.py check` still answers `ready` for all four merges (#960, #965, #966,
    #971), and `gh api repos/…/compare/<merge>...main` answers `ahead` for each, so the next
    morning's checkout of `main` carries all four.
  - `linear_ops.py dump-comments DRE-4541` returned the newest 50 comments. The newest
    `🧺 groom-proposal` among them is still `a4f198411b02`, and no proposal in the window carries
    `## Not ready` or `## Rewrite first`.
  - The cron lines on `main` are still `0 13 * * *` and `0 14 * * *`.
- **The first run:** proof run
  [38103553587](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38103553587)
  (`Proof Task`, dispatched for the first proof run), started 2026-10-10 18:57 PT on `main` at
  `c7cc71ce9bbf755b6fc6052e03d5fb2e27230a26`. Observed 2026-10-10 18:58–19:10 PT. What it saw
  follows.
- **The release.** `proof_release.py check` against the four build merges answered:

  ```
  proof-release: ready — pipeline-channel (the whole repository): stable carries #960 (3e09de8) for DRE-6664
  proof-release: ready — pipeline-channel (the whole repository): stable carries #965 (8709bd1) for DRE-6665
  proof-release: ready — pipeline-channel (the whole repository): stable carries #966 (f97f675) for DRE-6666
  proof-release: ready — pipeline-channel (the whole repository): stable carries #971 (0b23ca7) for DRE-6667
  ```

  The merges landed on 2026-10-10: #960 at 14:17 PT, #965 at 15:23 PT, #966 at 16:14 PT and #971 at
  17:12 PT. `stable` points at `eaf0e6c542e2154336cef08fafaa38d4af2dd87e`, a lightweight tag on a
  commit dated 2026-10-10 18:31 PT. On the board, DRE-6664, DRE-6665, DRE-6666 and DRE-6667 are
  `Done` (`linear_ops.py children-json DRE-6644`). `self-groomer.yml` calls the groomer `@main`, so
  the first morning after the release runs every change once it checks out `main`.
- **The mornings so far.** `gh run list --workflow self-groomer.yml`, newest first:

  | Run | Event | Started (PT) | Commit |
  | -- | -- | -- | -- |
  | 38070329055 | repository_dispatch (drain) | 2026-10-10 10:05 | `9447e7b2` |
  | 38058824979 | schedule (off-hour cron of the pair) | 2026-10-10 07:14 | `6d6a8434` |
  | 38054243567 | schedule (the morning) | 2026-10-10 06:03 | `6d6a8434` |

  No run of `self-groomer.yml` exists after 10:05 PT on 2026-10-10. `git merge-base --is-ancestor
  3e09de81 6d6a8434` fails, so the 2026-10-10 morning ran without even the first build merge. Its
  `post` job (114220979416) logged `CARD: DRE-4541` and posted proposal `a4f198411b02` at 06:12 PT.
- **The standing card.** `linear_ops.py dump-comments DRE-4541` returned 50 comments. The newest
  proposal in them is `a4f198411b02`, the 2026-10-10 morning's, and its sections are
  `## The population` through `## On cycles`. It has no `## Not ready — and what would make it
  ready`, no `## Rewrite first`, no `Not verified — not in the batch` line and no
  `in flight — serves` mark, because the code that writes them was not yet merged. No proposal on
  the card came from the released code.
- **The next morning.** The cron lines are `0 13 * * *` and `0 14 * * *` (UTC). The first scheduled
  morning after the release is 2026-10-11 06:00 PT, and the one after it is 2026-10-12 06:00 PT.
- **proof local run:** none. `PROOF_LOCAL_STATUS=none`. The card has no `Local screen:` row.

## Identities

- **github read** — `proof identity: github read — app agent-bureau-bot-2, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`.
  Used for every GitHub read above: the run list, run 38054243567's jobs and `post` log, the four
  pull requests, the `stable` ref and the `proof_release.py` check.
  - `gh variable get GROOM_PROPOSAL_CARD --repo dreadnought-foundry/bureau-pipeline` answered
    `HTTP 403: Resource not accessible by integration`, because the installation does not grant
    `variables: read`. The standing card was read instead from the 2026-10-10 morning's own `post`
    job log, `CARD: DRE-4541`, the card the card names.
  - **The refused write:**
    `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-38103553587 -f sha=c7cc71ce…`
    — GitHub answered `{"message":"Resource not accessible by integration", … "status":"403"}`.
    No ref was created.
  - **The second run (38104939908)** held the same token, by the same `proof identity: github read`
    line. Used for the run list, the run's own metadata, `main` and `stable`, the four compares,
    `proof_release.py check`, `self-groomer.yml` on `main`, and pull request #981's comments.
    **Its refused write:**
    `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-38104939908 -f sha=47073ee5…`
    — GitHub answered `{"message":"Resource not accessible by integration", … "status":"403"}`.
    No ref was created.
  - **The third run (38106722129)** held the same token. Used for the run list, its own run's
    metadata, `main`, `stable`, the eight compares, `self-groomer.yml` on `main`, and pull request
    #981's comments. **Its refused write:**
    `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-38106722129 -f sha=97f8f3ad…`
    — GitHub answered `{"message":"Resource not accessible by integration", … "status":"403"}`.
    No ref was created.
- **github write** — `GH_TOKEN`, the worker token. Used only to push this branch and open its pull
  request.
- **linear** — `LINEAR_API_KEY`, the fleet key. Reads: `dump-comments DRE-4541`, `state-of DRE-6678`
  (`Hand-work`), `epics-in-flight`, `children DRE-6644` and `children-json DRE-6644`. Writes: this
  run's heartbeats and its `agent-actor` marker on DRE-6678, nothing else.
  The first run counted linear requests: 13 of 40 when it pushed.
  The second run's reads: `dump-comments DRE-4541`. Its writes: its heartbeats and its
  `agent-actor` marker on DRE-6678, nothing else. It counted 9 of 40 when it pushed.
  The third run's reads: `dump-comments DRE-4541`. Its writes: its heartbeats and its
  `agent-actor` marker on DRE-6678, nothing else.
  linear requests: 9 of 40, counted for the third run when this record was pushed. The
  `⏳ 5/5 PR opened` heartbeat and the `agent-actor` marker come after the push, at about two
  requests each.
- **aws** — `proof identity: aws — none, PROOF_ROLE_ARN not provided`. No criterion here needs it.
- No scratch state was created.

## Criteria

| Criterion | Result |
| -- | -- |
| The first scheduled morning proposal after the release carrying DRE-6664 through DRE-6667 is live is observed on the standing card. If its page carries `## Not ready — and what would make it ready`, every row names a rule, a plain-English reason and a condition, and for each row the rule is re-checked against the board that morning and the record says whether the board bore it out; no card on that table is also listed under "Not now — and when to come back" or counted in that section's line, and none is in the Planning table. If the morning held no Not ready card, the page carries no such heading and the row is `Not observed.`, naming the morning. | ACCEPTED by operator decision — the clause was seen holding as the card words it on the second morning after the release (2026-10-12, proposal `06e997f2ba4c`), not the first, and only that timing differs from the card's words; the first morning's page named two Not ready cards under Not now, which DRE-6910 fixed before the second morning ([operator decision](https://github.com/dreadnought-foundry/bureau-pipeline/pull/981#issuecomment-6133780913), written by a session that took none of these readings). **First morning, 2026-10-11: not as the card words it.** Seen on proposal `ab09ece4ddee`, posted on DRE-4541 at 06:13 PT on 2026-10-11 by run 38141699804 (read with `linear_ops.py dump-comments DRE-4541` at 07:03 PT; the comment equals the run's `proposal-verified.txt` character for character). The page carries `## Not ready — and what would make it ready` with 21 rows over 18 cards, and every row has a Rule, a Reason and a Ready when cell: 2 `read-waits-on-open-card`, 11 `file-named-by-in-flight-card`, 4 `child-of-epic-not-in-progress`, 2 `undecided-question` and 2 `roll-up-parent`. Each rule was re-checked against the board as the run read it at 06:08 PT, and the board bore out all 21. Rows 1 and 5: DRE-6781 and DRE-6869 were epics in Green Light (since 21:33 PT on 2026-10-10 and 01:38 PT on 2026-10-11) with children still in Backlog; they moved to In Progress at 06:59 PT and 07:00 PT, after the page. Rows 2, 4, 6, 9, 10, 11, 12, 13, 14, 15 and 19: `groomer.file_references` over both cards' descriptions finds every file the row names on both sides, in the same repo, and each named card was in the lane the row gives: DRE-5997, DRE-6057, DRE-4669, DRE-4723, DRE-6639 and DRE-6535 In Progress; DRE-6902 In Progress from 06:04 PT (In Review at 06:25 PT, Done at 06:44 PT); DRE-6899 In Progress from 05:37 PT (In Review at 06:24 PT); DRE-6870 In Review (Done at 06:23 PT). Rows 7, 8, 20 and 21: DRE-3245, the parent of DRE-3238, and DRE-2284, the parent of DRE-3751, DRE-3749 and DRE-3750, are in Intake. Rows 17 and 18: DRE-4072 and DRE-3857 carry the open section quoted in the next row. Rows 3 and 16: the children DRE-4720, DRE-4721, DRE-4722 and DRE-4723 of DRE-4666, and DRE-5484, DRE-5485 and DRE-5486 of DRE-5483, are `[EPIC]` cards. None of the 18 cards is in the Planning table (14 rows), none is among the 24 cards counted by `24 cards are **not now**`, and none is in the ten bullets (15 cards) under `## Not now — and when to come back`. What is not as the card words it: that same section ends with the line `Named nothing: DRE-2709, DRE-5700 say they are superseded without naming what replaced them, so they are sequenced normally rather than proposed for Cancel.`, and both cards are on the Not ready table (row 15, and rows 5 and 6). So two Not ready cards are named under the Not now heading, in a line about something else, and that line says they are sequenced normally while the table holds them. **Second morning, 2026-10-12: the row holds as the card words it.** DRE-6910's fix (bureau-pipeline #1010, merged 08:04 PT on 2026-10-11, `67749a05`) is in `5d71a589`, the commit run 38255458434 checked out (`compare` answers `ahead` by 210). Proposal `06e997f2ba4c` (posted 06:32 PT) carries `## Not ready — and what would make it ready` with 22 rows over 19 cards, and every row has a Rule, a Reason and a Ready when cell: 1 `roll-up-parent`, 15 `file-named-by-in-flight-card`, 4 `child-of-epic-not-in-progress` and 2 `undecided-question`. Each rule was re-checked against the board. Rows 2 to 6, 9 to 16, 19 and 21: a search of both cards' descriptions in our database (15:18 PT) finds every file the row names on both sides, and each named card was in the lane the row gives when the run read the board at 06:27 PT (`groomer: readiness read 40 cards in flight and 2 named cards`): DRE-6639, DRE-6853, DRE-5997, DRE-6057, DRE-4669 and DRE-6829 In Progress; DRE-6898, DRE-6886, DRE-3721, DRE-6760 and DRE-6922 In Review. None of those eleven changed lane between 05:00 PT and 15:12 PT. This search is plainer than the first morning's `groomer.file_references` and does not check that both cards are in one repo. Rows 7, 8, 20 and 22: DRE-3245, the parent of DRE-3238, and DRE-2284, the parent of DRE-3751, DRE-3749 and DRE-3750, are in Intake. Row 1: the children DRE-4720, DRE-4721, DRE-4722 and DRE-4723 of DRE-4666 are epics. Rows 17 and 18: DRE-4072 and DRE-3857 carry the open section quoted in row 2. None of the 19 cards is in the Planning table (15 rows). None is among the 21 cards of the 18 bullets under `## Not now — and when to come back`, and none is among the 31 counted by `31 cards are **not now**` (the `not-now` outcome of `proposal-verified.json` holds 31 cards and shares none with its 19 `not-ready` cards). The section's last line is now `Named nothing: DRE-5700 say they are superseded without naming what replaced them, so they are sequenced normally rather than proposed for Cancel.` DRE-5700 is row 5 of the Planning table and on no Not ready row, so the line names no Not ready card. What differs from the card's words is only the morning: the card asks for the first morning after the release, and the clause held on the second. |
| If any card in the offer that morning carries an open "decide before building" heading with a list item beneath it and no `## Decisions from the CEO` block, read off the cards' descriptions, it is on the Not ready table under `undecided-question`, its Reason cell quotes that heading's first item, its Ready when cell says the section is settled on the card, it is not in the Planning table, and its state after the drain is still Intake; if no card in the offer carried such a heading, the row is `Not observed.` and names how many descriptions were read. | Met, on the second morning and its drain. The first morning's proposal `ab09ece4ddee` was never approved, so no drain followed it. What it showed is kept at the end of this cell. **2026-10-12:** our database (15:11 PT) finds `decide before building` in the descriptions of two cards in Intake or Planning, DRE-4072 and DRE-3857, and neither carries `## Decisions from the CEO`. Both are on proposal `06e997f2ba4c`'s Not ready table under `undecided-question`. Row 17, DRE-4072: Reason `open question: "Whether the twelve tiles are one rank or two ranks of six, …" (+2 more) under "What to decide before building"`. Row 18, DRE-3857: Reason `open question: "The console uses a pipeline access key and a two-phase cut …" (+1 more) under "To decide before building"`. Each has Ready when `that section is settled on the card`. Neither is in the Planning table (15 rows). The CEO approved the proposal in the console at 08:08 PT, and `🧺 groom-drained: 06e997f2ba4c` followed at 08:08 PT. At 15:06 PT both cards are in Intake, with no state change since 2026-09-15 (DRE-4072) and 2026-09-13 (DRE-3857). **2026-10-11:** our database (06:54 PT) held 70 cards in Intake, and a search of all 70 descriptions found `decide before building` on the same two. `linear_ops.py description` (07:03 PT) showed `## What to decide before building` on DRE-4072 with three list items, the first `Whether the twelve tiles are one rank or two ranks of six, at each width.`, and `## To decide before building` on DRE-3857 with two, the first beginning `The console uses a pipeline access key and a two-phase cut`. Both were on that morning's Not ready table as rows 17 and 18, with the same Reason and Ready when cells, and neither was in its Planning table. |
| The same page carries `## Rewrite first` for every `partly-solved` verdict on a card holding a Planning slot that morning, each listed card carries exactly one `🧺 groom-rewrite-draft` comment whose `body=` digest matches the card's current body, and no partly-solved card is in the Planning table; a morning with no `partly-solved` verdict is recorded as `Not observed.` for that row. | Met. The verify step answered `partly-solved` for four cards (`groom-verify: 27 card(s) — 14 still-needed, 4 partly-solved, …` in the `post` job log). Three held a Planning slot, each `list: planning` in the run's `verify-targets.json`: DRE-3603, DRE-3549 and DRE-3514. The fourth, DRE-2982, is `list: spare`: it held no slot, took none, and kept its ranked `not-now`. The page carries `## Rewrite first` with exactly the three slot holders, each row ending `(draft posted on the card)`. `linear_ops.py dump-comments <card> --with-authors` (07:03 PT) shows one `🧺 groom-rewrite-draft` comment on each, posted at 06:13 PT: `🧺 groom-rewrite-draft: ab09ece4ddee body=37fadff01aa2` on DRE-3603, `🧺 groom-rewrite-draft: ab09ece4ddee body=071d2c5c3884` on DRE-3549 and `🧺 groom-rewrite-draft: ab09ece4ddee body=65f86bff1232` on DRE-3514. `groomer.body_digest` over each card's description, read with `linear_ops.py description` at 07:03 PT, gives `37fadff01aa2`, `071d2c5c3884` and `65f86bff1232`. In our database the three drafts and the proposal share one author. None of the four partly-solved cards is in the Planning table. DRE-2982 carries no draft and is not on the list. **2026-10-12:** proposal `06e997f2ba4c` lists one card under `## Rewrite first`, DRE-3603, ending `(draft of 2026-10-11 stands on the card)`. No new draft was posted, and DRE-3603's body digest still reads `37fadff01aa2` in our database (15:20 PT), the `body=` of that draft. The page names DRE-3549 and DRE-3333 as partly solved spares with no slot and no draft. None of the three is in the Planning table. |
| No `unverified` card is in the Planning table, and for each one the page's "Verified against main" section lists it under `Not verified — not in the batch, each stays in Intake:` with its reason, and its state after the drain is Intake; a morning with no `unverified` verdict is `Not observed.` | Met, on the second morning and its drain. The verify step on 2026-10-12 answered `unverified` for one card (`groom-verify: 30 card(s) — 15 still-needed, 3 partly-solved, 3 done-elsewhere, 0 obsolete, 0 not-worth-it, 1 unverified, 8 excluded` in the `post` job log): DRE-3535, with `"reason": "no proof"` in its `groom-verdict-DRE-3535` artifact. It is not in the Planning table of proposal `06e997f2ba4c`. Under `## Verified against main` the page reads `Not verified — not in the batch, each stays in Intake:`, then `- DRE-3535 — no proof`. After the drain (08:08 PT) it is in Intake at 15:06 PT, with no state change since 2026-09-10. **2026-10-11:** the verify step answered `unverified` for DRE-3535 and DRE-2657, and the page listed both the same way. That proposal was never drained, and both cards are in Intake at 15:06 PT on 2026-10-12. |
| Within each band of the Planning table, every unit the page marks `in flight — serves DRE-N (In Progress)` precedes every unit it does not so mark, read net of the collision and `blockedBy` constraints the page names under its unhonoured constraints; a band with no in-flight unit, or a morning with none, is recorded as `Not observed.` | ACCEPTED by operator decision — the order was seen holding, read from the proposal's `serves` field because the page printed no `in flight — serves` mark on a judged row (DRE-7397); only that method differs from the card's words ([operator decision](https://github.com/dreadnought-foundry/bureau-pipeline/pull/981#issuecomment-6186420155)). **Groomer dry run [38694874202](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38694874202), 2026-10-14 23:50–23:58 PT** (`mode: propose`, `dry_run` on, proposal `40fbbe454ad8`, nothing posted), over the live Intake lane plus one sandbox fixture on `repo:agent-bureau-demo` placed at 23:50 PT and canceled after the run: epic DRE-7386 In Progress and its Intake child DRE-7387. The unit DRE-7387 carries `serves: DRE-7386`, band 2, sequence position 5, and is row 3 of the Planning table, first of the no-priority rows. Every other band-2 Planning unit comes after it (positions 11, 12, 13, 15, 17–21, 66, 76), and the two band-1 (High) units come before it (positions 1, 2). `unhonoured_constraints` names only DRE-7100/DRE-7096, neither in the batch. DRE-7387 was also the newest Intake card, so this reading shows the order holds but not which rule placed it. An earlier dry run (38692557021, 23:28–23:36 PT) read the live board's only in-flight Intake cards, DRE-7331 and DRE-7332 (epic DRE-7191). Both are `hand-built` and were excluded without judgment. The scheduled mornings of 2026-10-11 and 2026-10-12 held no in-flight unit (proposals `ab09ece4ddee`, `06e997f2ba4c`). |
| A card on the Not ready list one morning whose stated condition held by the next morning appears on the next morning's Planning list with no person adding it, read off the two proposal pages and the card's state between them; if no card's condition turned inside the two-morning window, the row says so as `Not observed.` | Met. Two cards on the 2026-10-11 Not ready list had every stated condition hold by the 2026-10-12 morning, and both are on that morning's Planning list with no person adding them. DRE-6767 was on proposal `ab09ece4ddee` as row 1 (`read-waits-on-open-card`, Ready when `DRE-6781 is built out or Done`) and row 2 (`file-named-by-in-flight-card`, Ready when `DRE-6902 is Done`). DRE-6902 went Done at 06:44 PT on 2026-10-11 and DRE-6781 at 16:52 PT. On proposal `06e997f2ba4c` DRE-6767 is row 3 of the Planning table and on no Not ready row. DRE-5700 was on it as row 5 (`read-waits-on-open-card`, Ready when `DRE-6869 is built out or Done`) and row 6 (`file-named-by-in-flight-card`, Ready when `DRE-6870 is Done`). DRE-6870 went Done at 06:23 PT on 2026-10-11, and DRE-6869 is built out: every child is Done or Canceled except its proof card DRE-6892 (In Review). On proposal `06e997f2ba4c` DRE-5700 is row 5 of the Planning table and on no Not ready row. Between the two pages neither card changed lane: our database holds no state event for either from 06:00 PT on 2026-10-11 until the drain moved each from Intake to Planning at 08:08 PT on 2026-10-12. No `🧺 groom-added` was posted on any card in the window, and the drain's record lists both as `moved`, at `proposal 06e997f2ba4c position 3` and `position 5`. |
| For every `now` verdict in the morning's judgement artifact whose `depends on` line names a card or an epic that is open on the board that morning and is not the card's own parent, that card is on the Not ready table under `read-waits-on-open-card` naming it, with the condition the rule states (Done for a card; built out or Done for an epic), and the board that morning bears the name out: the record quotes the line, the row and the named card's state, and for an epic the child that keeps it from being built out. Beside that, as evidence of how much of the read the rule could see and not as a judged measure, the record states how many `now` verdicts carried no `depends on` line, against the page's `depends_on_missing` count. A morning on which no `now` verdict named an open card, or whose read was declined or ran with no judgement, is `Not observed.` and says which. | ACCEPTED by operator decision — on both mornings a card whose read names an epic with real Backlog children was held under this rule, and a card whose read names an epic In Progress with only its proof card open was not. The CEO decided at about 21:00 PT on 2026-10-12 that an epic whose only open child is its proof card counts as built out for this hold, and the proof still runs and closes. So the criterion reads as an epic with buildable work left, and the rule was seen working under that reading on both mornings ([operator decision](https://github.com/dreadnought-foundry/bureau-pipeline/pull/981#issuecomment-6138024420), written by a session that took none of these readings; it replaces the earlier decline of this row). **First morning, 2026-10-11.** Two of four cards are not on the table as the card words it. The morning's `judgement-answer.txt` (`gh run download 38141699804 -n groom-proposal`, 07:02 PT) holds 20 `now` verdicts. Four have a `depends on` line that names a card, each named card was open that morning, and none is the card's parent. Two are on the Not ready table as the card says. `DRE-6767 … depends on: DRE-6781` has row 1: `read-waits-on-open-card`, Reason `its ranked read waits on DRE-6781 (Green Light), an epic with work left to build`, Ready when `DRE-6781 is built out or Done`. `DRE-5700 … depends on: DRE-6869` has row 5: `read-waits-on-open-card`, Reason `its ranked read waits on DRE-6869 (Green Light), an epic with work left to build`, Ready when `DRE-6869 is built out or Done`. The board bore both out at the run's read (06:08 PT): DRE-6781 was in Green Light with its children DRE-6785, DRE-6786 and DRE-6789 in Backlog, and DRE-6869 was in Green Light with DRE-6871 and DRE-6872 among its children in Backlog. Two are not on the table under this rule: `DRE-4666 … depends on: DRE-6390` and `DRE-3545 … depends on: DRE-6232`. DRE-6390 and DRE-6232 are epics In Progress, open on the board, and the rule read each as already built out, so it held neither card. The rule's own read at 07:03 PT (`groom_readiness.read_epics`, then `built_out`) answers `True` for both: the one open child of DRE-6390 is its proof card DRE-6400 (In Review since 09:45 PT on 2026-10-10), and the one open child of DRE-6232 is its proof card DRE-6241 (in Hand-work since 2026-10-08). DRE-4666 is on the table under `roll-up-parent` and not under `read-waits-on-open-card`. DRE-3545 is on no Not ready row: it is row 6 of the Planning table, and the page prints `**Depends on:** DRE-6232` beside it. The card asks for every such card to be on the table under this rule; the rule as built holds a card for a named epic until that epic is built out, and an epic In Progress with its proof card left counts as built out. Beside that, and not judged: all 20 `now` verdicts carry a `depends on` line (16 say `nothing`), so none is missing one, and `depends_on_missing` in `proposal-verified.json` is 0. The page text does not print that count. **Second morning, 2026-10-12: the same reading.** The `judgement-answer.txt` of run 38255458434 holds 10 `now` verdicts, each with a `depends on` line. Eight say `nothing`, and two name a card: `DRE-6933 … depends on: DRE-6390` and `DRE-6932 … depends on: DRE-6829`. Both named cards are epics In Progress, and in each the one open child is the epic's proof card: DRE-6400 (In Review) under DRE-6390, and DRE-6898 (In Review) under DRE-6829. Every other child is Done or Canceled (our database, 15:12 PT). Neither card is on the Not ready table under `read-waits-on-open-card`. DRE-6933 is row 1 of the Planning table. DRE-6932 is on the Not ready table only under `file-named-by-in-flight-card` (row 2). The rule behaved as on the first morning: an epic In Progress with only its proof card open counts as built out. Not judged: `depends_on_missing` is 0. |
| Inside the window, the drain's record gives every Rewrite first draft on the approved proposal its outcome: `rewritten` for one the CEO accepted with `🧺 groom-rewrite-accepted` (that card then carries a `🧺 groom-rewritten` comment holding its previous body, its description is the accepted draft, and it is in Planning or past it), or `held back` with `rewrite not accepted` for one the CEO did not accept, whose body digest is unchanged. The record says which branch the window held, and names a branch that was not exercised as not exercised. *(Reworded on the card on 2026-10-20 by the operator session; the old wording is on the card.)* | Met. The window held the not-accepted branch. Proposal `06e997f2ba4c` (2026-10-12) was the one approved, and it listed one Rewrite first draft, on DRE-3603. The CEO approved it at 08:08 PT without accepting the draft, and the drain (run 38268032518) recorded DRE-3603 as `held back` with `rewrite not accepted`. DRE-3603's body digest was still `37fadff01aa2`, the draft's `body=`, at 15:20 PT. Proposal `ab09ece4ddee` (2026-10-11) listed three drafts and was never approved or drained. No comment carrying `groom-rewrite-accepted`, `groom-rewritten` or `groom-added` was on any card after 06:00 PT on 2026-10-11, so the `rewritten` branch was not exercised. The reading is this record's own, as first written; only the row's wording changed. |
| The record names the `main` commit each morning's run checked out and the run ids. *(Split from the last criterion on 2026-10-20.)* | Met. the first morning's run is 38141699804 (`self-groomer.yml`, `schedule`, started 06:04 PT on 2026-10-11, green). Its `groom` job 114476736475 checked out `main` at `480b5cf5a55d6865552a62e8a3a0e30a30db50ae` (`Uses: …/groomer.yml@refs/heads/main (480b5cf5…)` in the job log), a commit dated 03:55 PT on 2026-10-11 that carries all four build merges, and its `post` job 114478567652 posted proposal `ab09ece4ddee` on DRE-4541 at 06:13 PT. The second morning's run is 38255458434 (`schedule`, started 06:24 PT on 2026-10-12, green). Its `groom` job 114832318494 and `post` job 114835446522 checked out `main` at `5d71a5891d68bdfd56db6827c0776c5b22049772`, and the `post` job posted proposal `06e997f2ba4c` on DRE-4541 at 06:32 PT. The drain is run 38268032518 (`repository_dispatch`, 08:08 PT, `12ac7585`). The earlier runs are 38054243567 (morning, `6d6a8434`), 38058824979 (off-hour cron), 38070329055 (drain, `9447e7b2`) and the three proof runs, 38103553587 (`c7cc71ce`), 38104939908 (`47073ee5`) and 38106722129 (`97f8f3ad`). |
| The record is merged to `main` from `agent/DRE-6678-proof-record`, with every judged row read by the critic. | Closing step: the merge of this pull request. |
| The CEO closes this card after reading the record | Open: the CEO's step |
