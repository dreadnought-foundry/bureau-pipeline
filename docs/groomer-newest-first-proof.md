# Groomer newest-first proof — one real morning, and the 2026-09-26 proposal replayed (DRE-4968)

**Status: PREPARED, NOT YET OBSERVED.** This file is a skeleton written on
2026-10-04 so that the reader of the 2026-10-05 06:00 PT morning only fills in
readings. Every `⟨FILL⟩` is a reading from the live run or the replay; nothing
below it is a result yet.

The proof for [DRE-4968](https://linear.app/dreadnoughtfoundry/issue/DRE-4968),
from epic DRE-4963. Every time is Pacific (PDT, UTC−7) and labeled PT.

**Why this file is not `docs/groomer-verify-proof.md`.** The card names that
path, and so does its sibling DRE-4973. Two pull requests that both create one
new file conflict, so DRE-4973 keeps the named path (its earlier record, #559,
used it) and this card's record is here. Nothing else about the card changes.

## Before reading: what is already known to be at risk

**§4 is expected to show the same gap as on 2026-09-30 and 2026-10-02, unless
something changed overnight.** On both mornings the pre-post check (layer A,
`groom_verify.merged_mentions`) printed `Unread: merged pull requests could not
be read this run for <every card>`. The 2026-10-02 groom log gives the reason:

```
groom context: merged_prs for DeltaSolv could not be read: the Bureau App token's installation cannot see it
groom context: merged_prs for EveryBite could not be read: the Bureau App token's installation cannot see it
```

Layer A searches merged PRs with ONE token, the Bureau App's
`dreadnought-foundry` installation. By design, a search that cannot see every
owner cannot say "nothing merged", so every card is `unread` on that source.
Nothing merged between 2026-10-02 and 2026-10-04 changes that.

**The same morning, the per-owner lookup legs DID read merged PRs per card,**
each on its owner's own token (DRE-5308/5429/5458):

```
groom-lookups: EveryBite — 18 card(s) read, 45 request(s) of a budget of 600, 20.9 s of 300 s
groom-lookups: dreadnought-foundry — 18 card(s) read, 197 request(s) of a budget of 600, 76.4 s of 300 s
groom-lookups: DeltaSolv — 18 card(s) read, 45 request(s) of a budget of 600, 20.2 s of 300 s
groom-lookups: folded 3 document(s) — 18 card(s) ok, 0 failed, 3 named no file
```

So there are two readings of "the groomer reads merged PRs". Layer A's
search is unread for every card. The verify agents' lookups were read per
owner, for the cards that name files. The reader puts both lines in §4 and
decides which one the card means. This record does not decide that in
advance. If the reader decides layer A's search is what counts, the fix is
for layer A to search each owner on that owner's token, the way the lookup
legs do. That goes to the epic as a mid-epic discovery
(`scripts/mid_epic.py discovery DRE-4963`), not as a new card.

## How the readings are taken

```
DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./morning-2026-10-05
```

(The read-off script ships with DRE-4973's record. Its full description is
there. It is read-only, tested on the 2026-10-02 run, and writes `summary.txt`,
`key-lines.txt` and `jobs-pt.tsv`.)

## 1. The run

| | Reading |
|---|---|
| Scheduled run | ⟨FILL: run id and URL⟩, event `schedule`, nobody dispatching |
| Gate line | ⟨FILL⟩ |
| Proposal id | ⟨FILL⟩ |
| Comment landed on DRE-4541 (Linear `createdAt`, PT) | ⟨FILL⟩ — by 06:30 PT? ⟨FILL⟩ |

## 2. The header's merged-PR count

Quoted from the page: ⟨FILL: `Ranked by … against N epics in flight, N merged PRs and N closed cards …`⟩

**Pass bar:** a number, not `UNKNOWN` and not "could not be read". (2026-10-02
read 733.)

## 3. The order

The Planning list in list order. The creation date comes from Linear (one
`get_issue` per card, read once each) or the console DB (`make db-read`):

| Pos | Card | Priority | Band | Created (PT) |
|---|---|---|---|---|
| ⟨FILL⟩ | | | | |

**Pass bar:** Urgent, then High, then everything else newest first. A
priority older than 21 days that was not re-confirmed is ranked as Medium
(DRE-5307), so check the `## Priorities re-ranked as Medium` section before
calling an out-of-order High a failure. A tie on the same day breaks by repo
order: agent-bureau, bureau-pipeline, portico.

## 4. The checks

Every card that layer A (`verification.moved_to_cancel`) or the verify agents
(`done-elsewhere`, `obsolete`, `not-worth-it`) moved to the Cancel list, with
its evidence quoted:

| Card | Which check | Evidence, quoted |
|---|---|---|
| ⟨FILL⟩ | | |

Every card marked unverified or unread, with the reason:

| Card | Source | Reason |
|---|---|---|
| ⟨FILL⟩ | | |

Both merged-PR readings, side by side, quoted from `key-lines.txt`:

```
⟨FILL: the layer A `Unread:` line, or its absence⟩
⟨FILL: one `groom-lookups: <owner> — …` line per owner, and the fold line⟩
```

The reader's call on which reading the card means, and why: ⟨FILL⟩

## 5. Nothing on the Planning list was already done

For every Planning card, the reader's own check on the day of writing: the
card's comments, and merged PRs that name it in each owner's repos
(`gh search prs --merged "DRE-N" --owner <owner>`, one per owner in
`config/repo-map.json`).

| Card | Merged PRs naming it | Comments say done? | Already done? |
|---|---|---|---|
| ⟨FILL⟩ | | | |

**Pass bar:** none already done. A morning with one is not a pass. Say so,
name why the checks missed it, and observe the next morning. This is the same
hand audit as DRE-5459 observation 1's overturn count, so one audit serves
both. Do it once and cite it in both.

## 6. Cost and time

⟨FILL: the `Verify step:` line, quoted, and the groom job's own minutes from `jobs-pt.tsv`⟩

## 7. "Don't do" stayed put

If the CEO pressed "Don't do" that morning, a `🧺 groom-excluded: <id> DRE-N`
comment stands on DRE-4541 for each. Read each such card's lane from Linear
afterwards:

| Card | Excluded at (PT) | Lane afterwards |
|---|---|---|
| ⟨FILL, or "not pressed this morning — not exercised"⟩ | | |

**Pass bar:** each card is where it was, not in Backlog (DRE-4961, merge `8cccd0e`,
contained in the latest console release, v1.6.265). Intake is held
(`INTAKE_HOLD=2026-10-02`), so no drain moves anything that morning either way.

## 8. The replay of proposal `b9eecae64787`

**The input.** Proposal `b9eecae64787` is kept whole as the `groom-proposal`
artifact of run 36244896914 (2026-09-26, artifact 10906269570, expires
2026-10-26 06:31 PT). sha256 of `proposal.json`:
`edfbecf26061d19a0e752ac380d4607e607d8c74ca97ac11cc6eb70ea023995b`.

```
gh run download 36244896914 -R dreadnought-foundry/bureau-pipeline -n groom-proposal -D p0926
```

**What the three cards were on that proposal:** DRE-2382 at Planning position
3, DRE-2897 at Planning position 8, and DRE-3526 already on the Cancel list
from the ranked read (`evidence: DRE-4630 supersedes it`).

**What each layer reads.** Layer A reads the Planning list, the next 10 spares
and the Cancel list. It checks DRE-2382 and DRE-2897 for done-ness, and
DRE-3526's Cancel for a real replacement. Layer B (the verify agent) reads
the Planning list and the spares only, so it never sees DRE-3526. This was
confirmed on 2026-10-04 by running layer A over the proposal with blank
credentials: 24 cards in the window, DRE-3526 among them as a `cancel` row.

**The commands.** These are evidence scripts, not product code:
`docs/evidence/DRE-4968/replay_layer_a.py` and
`docs/evidence/DRE-4968/replay_layer_b.sh`.

```
# layer A — reads only; needs LINEAR_API_KEY and GH_TOKEN (the Bureau App installation token)
python3 docs/evidence/DRE-4968/replay_layer_a.py <bureau-pipeline tree at main> p0926/proposal.json replay
# layer B — the verify leg's four steps per card, the agent on the operator's local Claude login
BP=<bureau-pipeline tree at main> OUT=replay CARDS="DRE-2897 DRE-2382" bash docs/evidence/DRE-4968/replay_layer_b.sh
```

Layer B over the whole window is 23 agent runs, about $9 and 25 minutes run one
after another. Over the two cards it covers, it is about $1. Record which one
was run.

**The replay reads today's board, not the 2026-09-26 board.** A card closed
since then is flagged by its state rather than by a check that would have
caught it on 09-26. Record each card's lane at replay time, so the reader can
tell the two apart.

| Card | Lane at replay (PT) | Layer A verdict and evidence | Layer B verdict and first `file:line` |
|---|---|---|---|
| DRE-2897 | ⟨FILL⟩ | ⟨FILL⟩ | ⟨FILL⟩ |
| DRE-2382 | ⟨FILL⟩ | ⟨FILL⟩ | ⟨FILL⟩ |
| DRE-3526 | ⟨FILL⟩ | ⟨FILL: cancel-stands / cancel-rejected, with the replacement it read⟩ | not in layer B's window |

**Pass bar:** each of the three is flagged with its evidence, and the command
or run id is given.

## What is not proven, and why

⟨FILL⟩

## The card's criteria

| Criterion | Result |
|---|---|
| The record holds one real morning with all eight sections, observed live | ⟨FILL⟩ |
| The merged-PR count is a number, and the Planning list is newest first | ⟨FILL⟩ |
| No card on that morning's Planning list was already done | ⟨FILL⟩ |
| The replay of `b9eecae64787` flags DRE-2897, DRE-3526 and DRE-2382 with evidence | ⟨FILL⟩ |
| The CEO closes this card after reading the record | Open |
