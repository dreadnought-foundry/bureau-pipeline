# Groomer newest-first proof — one real morning, and the 2026-09-26 proposal replayed (DRE-4968)

**Status: OBSERVED 2026-10-05, NOT A PASS.** The morning ran and posted on
time. Its merged-PR count is a number, and no card on its Planning list was
already done. Two criteria fail:

- **The served Planning list is not newest first.** Three cards from behind the
  list took the slots of three excluded cards, at those cards' positions and
  not at their own (§3).
- **The replay catches one of the three cards on evidence that existed on
  2026-09-26.** It flags all three, but two of them only on board state written
  after that proposal (§8).

A third finding, outside the criteria: the morning's one Cancel, DRE-4059, is
wrong (§4). Section 7 is not exercised yet. Nobody had pressed "Don't do" on
this proposal by 08:20 PT.

The proof for [DRE-4968](https://linear.app/dreadnoughtfoundry/issue/DRE-4968),
from epic DRE-4963. Every time is Pacific (PDT, UTC−7) and labeled PT. Read off
on 2026-10-05 by a proof helper. The replay was run by the operator's session
at 08:07–08:10 PT.

**Why this file is not `docs/groomer-verify-proof.md`.** The card names that
path, and so does its sibling DRE-4973. Two pull requests that both create one
new file conflict, so DRE-4973 keeps the named path (#714) and this card's
record is here. Nothing else about the card changes.

## How the readings are taken

```
DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./morning-2026-10-05
```

The read-off script ships with DRE-4973's record (#714). It is read-only, and
it ran 2026-10-05 08:01–08:02 PT. It writes `summary.txt`, `key-lines.txt` and
`jobs-pt.tsv`. Creation dates, states and comments were read once per card
from Linear between 08:05 and 08:15 PT. Merged PRs were searched per owner
(§5).

## 1. The run

| | Reading |
|---|---|
| Scheduled run | [37314573171](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37314573171), event `schedule`, nobody dispatching, created 06:08:51 PT, `success` |
| Gate line | `it is 06:08 PT and DRE-4541 is open — the groomer runs` |
| Proposal id | `d50db9746997` (cycle 15) |
| Comment landed on DRE-4541 (Linear `createdAt`, PT) | 06:18:07 PT (`13:18:07.424Z`). By 06:30 PT? **Yes**, with 11 min 52 s to spare |

The day's other run, [37323782562](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37323782562)
(07:19 PT), is the second cron line of the pair. Its gate says `it is 07:19
PT, outside the 06:00-06:59 PT grooming hour — this is the other cron of the
pair and it does nothing`, and it did nothing.

## 2. The header's merged-PR count

Quoted from the page: `Ranked by claude-fable-5-1 (asked) / claude-fable-5-1
(answered) in 1 call over 136 cards, against 20 epics in flight, 940 merged PRs
and 60 closed cards — 131 of 136 cards ranked (1 the model declined, 1 never
reached).`

**Pass bar: met.** The count is 940, a number. (2026-10-02 read 733.)

## 3. The order

This is the Planning list as served, in list order. Priority and creation time
come from Linear (one `get_issue` per card). "Sequence" is the card's place
in the proposal's own rule order, `proposal-verified.json` → `sequence`. The
ordering key works in UTC days (`groomer.py` `_day_ordinal`), so the UTC date
is shown where it differs from the PT date.

| Pos | Card | Priority | Band | Repo | Created (PT) | Sequence |
|---|---|---|---|---|---|---|
| 1 | DRE-5239 | High | High | agent-bureau | 2026-09-29 13:33 | 1 |
| 2 | DRE-5202 | High | High | agent-bureau | 2026-09-29 10:32 | 2 |
| 3 | DRE-4874 | High | High | agent-bureau | 2026-09-25 09:41 | 3 |
| 4 | DRE-4872 | High | High | bureau-pipeline | 2026-09-25 09:33 | 4 |
| 5 | **DRE-5564** | none | rest | bureau-pipeline | 2026-10-01 20:33 (10-02 UTC) | **22**, in DRE-4666's slot |
| 6 | DRE-5654 | none | rest | portico | 2026-10-02 14:50 | 6 |
| 7 | DRE-4485 | High | High | portico | 2026-09-21 13:19 | 7 |
| 8 | DRE-5767 | none | rest | agent-bureau | 2026-10-04 07:55 | 9 |
| 9 | DRE-5761 | none | rest | agent-bureau | 2026-10-03 17:45 | 10 |
| 10 | DRE-5729 | none | rest | agent-bureau | 2026-10-03 11:19 | 11 |
| 11 | **DRE-5662** | none | rest | portico | 2026-10-02 16:41 | **23**, in DRE-5727's slot |
| 12 | DRE-5726 | none | rest | agent-bureau | 2026-10-03 10:36 | 13 |
| 13 | DRE-5721 | none | rest | agent-bureau | 2026-10-03 07:22 | 14 |
| 14 | DRE-5720 | none | rest | agent-bureau | 2026-10-03 07:22 | 15 |
| 15 | DRE-5746 | none | rest | bureau-pipeline | 2026-10-03 13:27 | 16 |
| 16 | DRE-5745 | none | rest | bureau-pipeline | 2026-10-03 13:26 | 17 |
| 17 | DRE-5744 | none | rest | bureau-pipeline | 2026-10-03 13:26 | 18 |
| 18 | **DRE-5658** | none | rest | portico | 2026-10-02 15:44 | **24**, in DRE-5700's slot |
| 19 | DRE-5676 | Medium | rest | portico | 2026-10-02 17:35 (10-03 UTC) | 20 |
| 20 | DRE-5645 | none | rest | agent-bureau | 2026-10-02 13:40 | 21 |

**The rule order is right.** The `sequence` the rules wrote is Urgent (none),
then High, then the rest newest UTC day first, with repo order as the
tie-break inside a day. One constraint holds DRE-5654 ahead of DRE-4485 at #6
and #7. That is a recorded collision (`DRE-5654 before DRE-4485 — both touch
provision-portal.sh; newer card first, and the order is recorded`), and the
page's own rule allows it ("unless a collision … the constraint wins"). None of
the 21 cards blocks another (Linear relations, read 08:05–08:15 PT).

**The served list is not.** The verify step excluded three cards without
judgement: DRE-4666 (sequence #5, "parent epic with 3 open children"), and
DRE-5727 (#12) and DRE-5700 (#19), both "moved into Intake on 2026-10-04". It
then filled each slot with the next still-needed card from behind the list,
at the slot's own position. The cause is `scripts/groom_verify_agent.py:925`
on `main`:

```
row.update(identifier=taker, position=slot["position"],
```

So DRE-5564 (created 10-01 PT), DRE-5662 (10-02) and DRE-5658 (10-02) sit at
#5, #11 and #18, ahead of cards created on 10-03 and 10-04. The page still
says "newest first" above them, and gives each the reason `in the batch by the
rules — newest first, position N`. The observation 1 dry run at 08:04 PT
(DRE-5459) shows the same thing: DRE-5658 sits at #5 there.

**Pass bar: not met.** The served Planning list breaks newest first at three
positions. If the bar is the rules' order, the fix is for a promoted spare to
take its place by the sequence, not the excluded card's slot. That goes to the
epic as a mid-epic discovery (`scripts/mid_epic.py discovery DRE-4963`). This
record does not file it.

## 4. The checks

Every card that layer A (`verification.moved_to_cancel`) or the verify agents
(`done-elsewhere`, `obsolete`, `not-worth-it`) moved to the Cancel list, with
its evidence quoted:

| Card | Which check | Evidence, quoted | The reader's check |
|---|---|---|---|
| DRE-4059 | layer A, merged pull requests | `already done — the merged pull request https://github.com/dreadnought-foundry/bureau-pipeline/pull/415 is for this card` | **Wrong.** #415 is `DRE-4058: the one-off route spends MAX_ROUNDS and then asks for a rewrite, not a sixth answer`, branch `agent/DRE-4058-one-off-round-bound`, body `Closes DRE-4058.`. It names DRE-4059 only as the sibling still to build: "The sibling card DRE-4059 covers who gets asked at all, and writing the CEO's signed answer into the card body — the two defects behind rounds 2 and 4." DRE-4059 is in Intake and is not done |

The verify agents moved no card to Cancel: `done-elsewhere: 0`, `obsolete: 0`,
`not-worth-it: 0`.

Every card marked unverified or unread, with the reason:

| Card | Source | Reason |
|---|---|---|
| DRE-5483 (behind the list) | verify agent | `no proof`: "the missing button and panel in Portico and Agent Bureau, which are other repos and cannot be seen in bureau-pipeline's main" |
| all 30 cards layer A reads | layer A, merged pull requests | `the Bureau App token's installation cannot see DeltaSolv, EveryBite` |
| DRE-4666, DRE-5727, DRE-5700, DRE-5656, DRE-5657 | verify step, excluded without judgement | parent epic with 3 open children; moved into Intake on 2026-10-04 (twice); hand-built (twice) |

Both merged-PR readings, side by side, quoted from `key-lines.txt`:

```
- Unread: merged pull requests could not be read this run for DRE-4059, DRE-4485, DRE-4666, … DRE-5767 — none of those cards is marked clean on that source.   (30 cards)
groom-lookups: EveryBite — 21 card(s) read, 45 request(s) of a budget of 600, 16.6 s of 300 s
groom-lookups: DeltaSolv — 21 card(s) read, 45 request(s) of a budget of 600, 18.8 s of 300 s
groom-lookups: dreadnought-foundry — 21 card(s) read, 201 request(s) of a budget of 600, 81.1 s of 300 s
groom-lookups: folded 3 document(s) — 21 card(s) ok, 0 failed, 3 named no file
```

**The reader's call.** The card's words "reads merged PRs" are met by §2's
count and by the per-owner lookups, which read every owner on its own token
for the 21 file-naming cards. Layer A's own search is still unread for every
card, on the 2026-10-02 cause. It can still find a hit inside
dreadnought-foundry, and that is how it reached DRE-4059. Its one hit this
morning was a false one: a PR that names a card as unfinished work was read as
that card's PR. Both points belong to epic DRE-4963 as discoveries. The unread
search is already DRE-5746, which sits on this Planning list at #15. The false
match is not filed anywhere yet.

## 5. Nothing on the Planning list was already done

This is the reader's own check, made on 2026-10-05 between 08:05 and 08:15 PT.
For each card: one Linear read of the card and its comments, and `gh search
prs --merged "DRE-N" --owner <owner>` for dreadnought-foundry, EveryBite and
DeltaSolv (63 searches). Every hit was opened, and hits that only matched part
of a number were dropped. No merged PR in any owner is on an `agent/DRE-N-`
branch for any of the 20 cards, or carries the card's id in its title. All 20
cards are in Intake.

| Card | Merged PRs naming it | Comments say done? | Already done? |
|---|---|---|---|
| DRE-5239 | agent-bureau#2943 (09-30): the DRE-5031 proof record that filed it | no | no |
| DRE-5202 | none | no | no |
| DRE-4874 | none | no | no (agent: `partly-solved`) |
| DRE-4872 | none | no | no. The first Linear release was written by hand on 09-25, and nothing writes one on promotion |
| DRE-5564 | none | no | no |
| DRE-5654 | none | no | no. Only the sandbox roster was fixed by hand, and `provision-portal.sh` is unchanged |
| DRE-4485 | none | no | no |
| DRE-5767 | none | "still worth doing" | no |
| DRE-5761 | agent-bureau#3124 (10-04): a DRE-5669 report naming it as a cause | "still worth doing" | no |
| DRE-5729 | none | "still worth doing" | no |
| DRE-5662 | portico#884 (10-04): the DRE-4453 record, which re-ran observation 1's file half | the description says observation 1's file half is done | **partly**. Observations 2 and 3 and the grant cleanup are open, so the card still has work |
| DRE-5726 | none | no | no |
| DRE-5721 | none | "still worth doing"; re-checked on `main` 10-03 | no |
| DRE-5720 | none | "still worth doing"; re-checked on `main` 10-03 | no |
| DRE-5746 | none | no | no (agent: `partly-solved`) |
| DRE-5745 | none | no | no |
| DRE-5744 | none | no | no |
| DRE-5658 | portico#878, #880 (10-02): DRE-5343 records listing it as pending | no | no |
| DRE-5676 | portico#891 (10-02): the DRE-4463 record, `Download FAIL: DRE-5676` | "nothing was built" | no |
| DRE-5645 | none | no | no |

**Pass bar: met.** No card was already done. DRE-5662 is partly done and has
work left, so it is not a done card on the list. The limit of this check: it
reads merged PRs and Linear text, not the code on `main`. The verify agents
read the code, and the DRE-4973 record (#714) hand-checks two of their proofs.
This audit serves DRE-5459 observation 1's overturn count too.

## 6. Cost and time

`Verify step: 29 cards, $4.84, 1 min 8 s wall clock`. The groom job ran
06:09:02–06:14:33 PT (5 min 31 s). The whole run, gate start to post end, ran
06:08:54–06:18:12 PT (9 min 18 s).

## 7. "Don't do" stayed put

**Not exercised yet.** DRE-4541 was read at 08:07 PT and again at 08:20 PT.
It carries no `🧺 groom-excluded: d50db9746997 …` comment and
no approval. The CEO may answer this proposal today (DRE-4716's sitting). If he
presses "Don't do", this section is read then: each card's lane from Linear,
expected still Intake, not Backlog. The Intake hold was lifted at 08:03 PT
(`INTAKE_HOLD` deleted), so an Approve would now drain.

## 8. The replay of proposal `b9eecae64787`

**The input.** Proposal `b9eecae64787`, kept whole as the `groom-proposal`
artifact of run 36244896914. `generated_at` is `2026-09-26T13:31:54Z`, which is
**06:31:54 PT**. The sha256 of `proposal.json` is
`edfbecf26061d19a0e752ac380d4607e607d8c74ca97ac11cc6eb70ea023995b`, checked
2026-10-05.

**How it was run.** The operator's fullstack session ran it on the CEO's go,
2026-10-05 08:07–08:10 PT. It used bureau-pipeline `main` at `111a5e717` (a
tarball) and the two scripts on this branch, which are byte-identical to the
ones it ran (sha256 checked):

```
python3 docs/evidence/DRE-4968/replay_layer_a.py ./bp-main p0926/proposal.json ./replay
BP=$PWD/bp-main OUT=$PWD/replay CARDS="DRE-2897 DRE-2382" bash docs/evidence/DRE-4968/replay_layer_b.sh
```

Run notes, as printed:
- `LINEAR_API_KEY` was unset, so `linear_ops` used the operator-tools key
  (`budget: undeclared`), not the fleet's.
- Layer A spent 16 Linear calls (`linear-calls: 25 request(s)`).
- Layer B's `targets` step printed `refused after 33 calls` and still finished.
- Layer B ran `claude-sonnet-5-5`, effort `high`.
- Each verdict reads `lookup: not-run`, because a local replay has no per-owner
  lookup legs.
- Neither layer wrote to Linear or GitHub.

**The replay reads today's board, not the 2026-09-26 board.** So for each
card, the date of the evidence is set against 06:31:54 PT on 09-26:

| Card | Lane at replay (read from Linear 2026-10-05, after the replay) | Layer A verdict and evidence | Layer B verdict and first `file:line` | Evidence dated before the proposal? |
|---|---|---|---|---|
| DRE-2897 | Canceled (09-26 08:58 PT) | **`cancel`**, source `comments`. The 2026-09-05 comment: "Superseded, 2026-09-05. The CEO decided this on DRE-2895 … that epic now carries the work as its child DRE-3230". Also the 09-26 08:48 PT comment: "Nothing left to build: this defect was already fixed and shipped under DRE-3230, merged on 2026-09-06 (pull request #431…)" | **`done-elsewhere`**, `client/app/src/features/viewer/sync/useAnswerSync.ts:371` `s.clearsStatus.has(field) ? null : undefined,`. "This landed under DRE-3230, not under this card." | **Yes.** The 09-05 comment and the DRE-3230 code (merged 09-06) both predate it |
| DRE-2382 | Canceled (09-26 08:59 PT) | **`unread`** (`merged_prs`), no evidence | **`not-worth-it`**, `infra/lambda/legacy_migration_lib.ts:886` `parentId: UNFILED_FOLDER_ID,`. "The flaw is still in the code … The card's own cancel comment says every portal it could have hit is already migrated or retired" | **No.** The code proofs show the flaw is still there. The "not worth it" rests on the CEO's cancel comment of 09-26 08:59 PT, written after the proposal |
| DRE-3526 | Canceled (09-26 08:38 PT) | **`cancel-rejected`**, source `replacement`: "DRE-4630 was closed as Canceled, so nothing replaced it". DRE-3526 is in layer A's window (`DRE-3526 in the check's window: True`) | not in layer B's window (it was on the Cancel list) | **No.** DRE-4630 was canceled on 2026-09-29 at 12:51 PT. At 06:31 PT on 09-26 it sat open in Intake, so the same check would have let the cancel stand |

**Pass bar, read strictly: not met.** All three cards are flagged with
evidence today. Only **DRE-2897** is caught on evidence that existed when
`b9eecae64787` was written, and both layers catch it. DRE-2382 and DRE-3526
are flagged by what happened to the board after it. The replay does not show
that the 09-26 run would have caught them. A replay against the board as it
stood on 09-26 is not possible with today's reads.

Layer A also moved seven other cards to Cancel in that window: DRE-2458,
DRE-2462, DRE-2480, DRE-4627, DRE-4628, DRE-4631 (merged portico PRs), and
DRE-4629 ("DRE-5009 (Done) says: It replaces DRE-4629"). This record does not
date their evidence, because the card does not ask about them.

## What is not proven, and why

- **§7.** No "Don't do" was pressed on this proposal by the time of writing.
  It is read if the CEO answers today. Otherwise the next morning he presses
  one is read.
- **Newest first** fails on the served list (§3). A fix and a later morning
  are needed. The route is a mid-epic discovery on DRE-4963, which the operator
  files.
- **The replay** catches one of three on evidence from its own day (§8).
  Whether that meets the card's intent is the CEO's call. Read strictly, it does
  not.
- **DRE-4059's Cancel is wrong** (§4). If the CEO approves this proposal as
  served, the drain would cancel a card whose work has not shipped. He should
  press "Don't do" on it.

## The card's criteria

| Criterion | Result |
|---|---|
| The record holds one real morning with all eight sections, observed live | **Partly met.** Sections 1–6 and 8 are read off run 37314573171 and the replay. Section 7 is not exercised yet (no "Don't do" pressed) |
| The merged-PR count is a number, and the Planning list is newest first | **Not met.** The count is 940, a number. The served list breaks newest first at #5, #11 and #18, where spares took excluded cards' slots (`groom_verify_agent.py:925`) |
| No card on that morning's Planning list was already done | **Met.** None of the 20. DRE-5662 is partly done and has work left (§5) |
| The replay of `b9eecae64787` flags DRE-2897, DRE-3526 and DRE-2382 with evidence | **Not met, read strictly.** All three are flagged. Only DRE-2897's evidence predates the proposal. DRE-2382 (layer B, from the CEO's later cancel comment) and DRE-3526 (layer A, from DRE-4630's 09-29 cancel) are flagged by board state written after it (§8) |
| The CEO closes this card after reading the record | Open |
