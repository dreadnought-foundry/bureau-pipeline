# PROOF record — DRE-6678: one morning's groom proposal keeps Not ready cards off the list, quotes an undecided question on it, rewrites partly solved cards first, and leaves an unverified card out with its reason (epic DRE-6644)

**Status: PARTIAL.**

No morning has run on the new code yet. The release carrying DRE-6664 through
DRE-6667 went live on 2026-10-10 at 18:31 PT. The last scheduled morning ran
at 06:03 PT that day, twelve hours earlier, on a commit that carries none of
them. The first morning the card asks about is the scheduled run of
2026-10-11 at 06:00 PT. Every judged row below waits for that morning, or for
the morning after it.

The critic held this record at 19:03 PT on 2026-10-10 for that reason, and a
second proof run was dispatched at 19:22 PT, before the morning. It found the
same board: no `self-groomer.yml` run after 10:05 PT, and no proposal on the
standing card newer than `a4f198411b02`. Nothing changed in the rows below
except the run ids.

The critic held it again at 19:26 PT for the same reason, and a third proof
run was dispatched at 19:55 PT, still before the morning. It found the same
board again. The rows below wait on the morning of 2026-10-11, and a run
dispatched before it can add only its own run id.

## How this was recorded

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
| The first scheduled morning proposal after the release carrying DRE-6664 through DRE-6667 is live is observed on the standing card. If its page carries `## Not ready — and what would make it ready`, every row names a rule, a plain-English reason and a condition, and for each row the rule is re-checked against the board that morning and the record says whether the board bore it out; no card on that table is also listed under "Not now — and when to come back" or counted in that section's line, and none is in the Planning table. If the morning held no Not ready card, the page carries no such heading and the row is `Not observed.`, naming the morning. | Not observed. waiting for the scheduled `self-groomer.yml` morning of 2026-10-11 06:00 PT, the first after the release went live at 18:31 PT on 2026-10-10: its proposal on DRE-4541, and whether that page carries `## Not ready — and what would make it ready`, with each row's rule re-checked against the board that morning. The only morning so far, run 38054243567 at 06:03 PT on 2026-10-10, ran on `6d6a8434`, which carries none of the four merges. Its proposal `a4f198411b02` has no such heading. |
| If any card in the offer that morning carries an open "decide before building" heading with a list item beneath it and no `## Decisions from the CEO` block, read off the cards' descriptions, it is on the Not ready table under `undecided-question`, its Reason cell quotes that heading's first item, its Ready when cell says the section is settled on the card, it is not in the Planning table, and its state after the drain is still Intake; if no card in the offer carried such a heading, the row is `Not observed.` and names how many descriptions were read. | Not observed. waiting for the scheduled morning of 2026-10-11 06:00 PT: its offer, the descriptions of the cards in it read for an open "decide before building" heading, the `undecided-question` rows on its Not ready table, and each such card's state after that morning's drain. No description was read for this, because no post-release offer exists. |
| The same page carries `## Rewrite first` for every `partly-solved` verdict on a card holding a Planning slot that morning, each listed card carries exactly one `🧺 groom-rewrite-draft` comment whose `body=` digest matches the card's current body, and no partly-solved card is in the Planning table; a morning with no `partly-solved` verdict is recorded as `Not observed.` for that row. | Not observed. waiting for the scheduled morning of 2026-10-11 06:00 PT: its `partly-solved` verdicts, the page's `## Rewrite first` section, and one `🧺 groom-rewrite-draft` comment per listed card with a `body=` digest matching the card's body. The 2026-10-10 page has no `## Rewrite first` section, because #966 had not merged when it ran. |
| No `unverified` card is in the Planning table, and for each one the page's "Verified against main" section lists it under `Not verified — not in the batch, each stays in Intake:` with its reason, and its state after the drain is Intake; a morning with no `unverified` verdict is `Not observed.` | Not observed. waiting for the scheduled morning of 2026-10-11 06:00 PT: its `unverified` verdicts, the `Not verified — not in the batch, each stays in Intake:` list under "Verified against main", and each such card's state after the drain. The 2026-10-10 page's `## Verified against main` section has no such list. |
| Within each band of the Planning table, every unit the page marks `in flight — serves DRE-N (In Progress)` precedes every unit it does not so mark, read net of the collision and `blockedBy` constraints the page names under its unhonoured constraints; a band with no in-flight unit, or a morning with none, is recorded as `Not observed.` | Not observed. waiting for the scheduled morning of 2026-10-11 06:00 PT: its Planning table, banded, with every `in flight — serves DRE-N (In Progress)` unit checked to come before the unmarked units in its band, net of the constraints the page names. The 2026-10-10 page carries no `in flight — serves` mark. |
| A card on the Not ready list one morning whose stated condition held by the next morning appears on the next morning's Planning list with no person adding it, read off the two proposal pages and the card's state between them; if no card's condition turned inside the two-morning window, the row says so as `Not observed.` | Not observed. waiting for the scheduled mornings of 2026-10-11 and 2026-10-12, 06:00 PT: the two proposal pages on DRE-4541, and for any card on the first page's Not ready list whose condition held by the second, its place on the second page's Planning list and its state between them. |
| For every `now` verdict in the morning's judgement artifact whose `depends on` line names a card or an epic that is open on the board that morning and is not the card's own parent, that card is on the Not ready table under `read-waits-on-open-card` naming it, with the condition the rule states (Done for a card; built out or Done for an epic), and the board that morning bears the name out: the record quotes the line, the row and the named card's state, and for an epic the child that keeps it from being built out. Beside that, as evidence of how much of the read the rule could see and not as a judged measure, the record states how many `now` verdicts carried no `depends on` line, against the page's `depends_on_missing` count. A morning on which no `now` verdict named an open card, or whose read was declined or ran with no judgement, is `Not observed.` and says which. | Not observed. waiting for the scheduled morning of 2026-10-11 06:00 PT: its judgement artifact downloaded with `gh run download`, each `now` verdict's `depends on` line, the `read-waits-on-open-card` rows on its Not ready table, the named cards' states that morning, and the count of `now` verdicts with no `depends on` line against the page's `depends_on_missing`. |
| If the CEO accepted a Rewrite first draft inside the window with `🧺 groom-rewrite-accepted`, the drain's record counts the card under `rewritten`, the card carries a `🧺 groom-rewritten` comment holding its previous body, its description is the accepted draft, and it is in Planning or past it; if he accepted none, the row is `Not observed.` and says so. | Not observed. waiting for the two-morning window of 2026-10-11 and 2026-10-12 to close: whether any `🧺 groom-rewrite-accepted` was posted on DRE-4541, and for each one the drain's `rewritten` count, the card's `🧺 groom-rewritten` comment, its description, and its state. No Rewrite first draft has been posted yet, so none can have been accepted. |
| The record names the `main` commit the morning's run checked out and the run ids, and it is merged on `agent/DRE-<n>-proof-record` with every judged row read by the critic. | Not observed. waiting for the scheduled morning of 2026-10-11 06:00 PT: its run id and the `main` commit it checked out, recorded here. This record already names every run so far: 38054243567 (morning, `6d6a8434`), 38058824979 (off-hour cron), 38070329055 (drain, `9447e7b2`) and the three proof runs, 38103553587 (`c7cc71ce`), 38104939908 (`47073ee5`) and 38106722129 (`97f8f3ad`). It is on `agent/DRE-6678-proof-record` and not yet merged. |
| The CEO closes this card after reading the record | Open: the CEO's step |
