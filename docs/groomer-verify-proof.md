# Groomer verify proof — one scheduled 06:00 PT morning, read off live state (DRE-4973)

**Status: PREPARED, NOT YET OBSERVED.** This file is a skeleton written on
2026-10-04 so that the reader of the 2026-10-05 06:00 PT morning only fills in
readings. Every `⟨FILL⟩` is a reading from the live run; nothing below it is a
result yet. Until every `⟨FILL⟩` is gone this record proves nothing.

The proof for [DRE-4973](https://linear.app/dreadnoughtfoundry/issue/DRE-4973),
from epic DRE-4967. Every time is Pacific (PDT, UTC−7) and labeled PT. The
shape follows `docs/groomer-two-lists-proof.md`: what was read, when, and what
it said, with the run and comment links.

## Why this morning, and not the "first morning after the wiring card"

The card asks for "the first scheduled morning run after the wiring card
merged". That morning was 2026-09-28 (run 36426267250, every leg unverified
before DRE-5123), and 2026-09-29 (run 36572881258) was recorded in
bureau-pipeline #559, which the CEO reopened at 11:35 PT that day: the verify
step had passed old cards as `still-needed`, so a morning only counts once its
proposal survives a real check. The verify step was rebuilt under DRE-5213
(last merge DRE-5348, #659, 2026-10-02 12:09 PT). The 2026-10-02 morning was
judged not to qualify (DRE-4973 comment, 06:53 PT). The groomer was then off
from the fleet pause (2026-10-02 15:17 PT) until it was turned back on, proposals
only, on 2026-10-04 at 07:31 PT. **2026-10-05 is the first morning after all of
that.**

## Before reading: three things already known

1. **The page does not print `file:line`.** `groomer.py`'s `_verdict_line`
   prints the verdict and the agent's summary only. The proofs are in the
   `groom-verdict-*` artifacts and in `proposal-verified.json`. On 2026-10-02
   every `still-needed` Planning card carried three or more `file:line` proofs
   there (for example DRE-5239 → `console/backend/release_brake.py:298`). The
   card's title says "file:line proof on every Planning card"; its acceptance
   criteria ask only for every leg's verdict and ONE proof checked by hand.
   This record reads the criteria, takes the proofs from the artifact, and says
   so. Printing them on the page would be a separate change.
2. **The verdict vocabulary changed after this card was written.** The card
   names `still-needed`, `done`, `obsolete`, `unverified`. Since DRE-5304 the
   agent answers one of `still-needed`, `partly-solved`, `done-elsewhere`,
   `obsolete`, `not-worth-it`, and the runner adds `unverified` and `excluded`.
   The table in §2 uses the current words.
3. **The vericorr question.** On 2026-10-02 DRE-5270 sat on the Planning list
   as `unverified — repo not in config/repo-map.json: vericorr`. vericorr left
   the platform. If a vericorr card is on the list again, name it under
   "unverified" with that reason. It does not fail the criterion, which asks
   that every unverified card be named with its reason.

## How the readings are taken

Read-only. Run after the post job finishes (expect about 06:20 PT):

```
DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./morning-2026-10-05
```

It lists the morning's runs, picks the `schedule` run created at 13:xxZ, writes
every job's start and end in PT, pulls the gate, groom, lookup, post and one
verify log PER JOB (`gh run view <run> --log` on the whole run fails with "too
many API requests"), downloads `groom-proposal-verified`, `groom-proposal` and
every `groom-verdict-*`, and prints the counts, the Planning list with each
card's verdict and first proofs, and the Cancel list. It was tested against the
2026-10-02 run (37010744859) on 2026-10-04.

The PT time the comment landed is read from Linear, not from the log: the
`🧺 groom-proposal: <id>` comment on DRE-4541, its `createdAt`, converted to PT.
The post job's `commented on DRE-4541` log line is the cross-check.

## 1. The run, the gate and the landing time

| | Reading |
|---|---|
| Run | ⟨FILL: run id and URL⟩, event `schedule` |
| Gate `why`, quoted | ⟨FILL: `it is 06:0x PT and DRE-4541 is open — the groomer runs`⟩ |
| Other cron of the pair | ⟨FILL: run id, `it is 07:0x PT, outside the 06:00-06:59 PT grooming hour …`⟩ |
| Proposal id | ⟨FILL⟩ |
| Comment landed on DRE-4541 (Linear `createdAt`, PT) | ⟨FILL⟩ |
| Before 06:30 PT? | ⟨FILL: yes, with N minutes to spare / no, late by N minutes⟩ |

Jobs, PT (from `jobs-pt.tsv`):

```
⟨FILL: gate, schedule / groom, each lookup leg, first verify start, last verify end, schedule / post⟩
```

**Pass bar:** a `schedule` run, nobody dispatching it; the gate line quoted;
the comment's `createdAt` before 06:30:00 PT. If it lands after 06:30 PT, say by
how much and which job carried the overrun.

## 2. Every verify leg, and the cost and wall clock

From `proposal-verified.json` (`verify.counts`, and each row's `verify`):

| # | Card | List | Verdict | First proof (`file:line`) or reason |
|---|---|---|---|---|
| ⟨FILL: one row per verify leg⟩ | | | | |

Counts: ⟨FILL: copy the `## Verified against main` counts block⟩

The page's line, quoted: ⟨FILL: `Verify step: N cards, $X.XX, M min S s wall clock`⟩

| Measure | Reading | Epic's prediction | Inside? |
|---|---|---|---|
| Cost | ⟨FILL⟩ | $8–30 | ⟨FILL: yes / below by $N / above by $N⟩ |
| Wall clock | ⟨FILL⟩ | 4–6 min | ⟨FILL⟩ |

On 2026-09-29 these were $4.81 / 1 min 29 s, and on 2026-10-02 $4.39 / 1 min
40 s, both under the prediction. A third reading below it means the
prediction, not the run, is off. Say so in one line.

**Pass bar:** every leg listed with its verdict, the line quoted, and both
in-or-out statements made.

## 3. One proof opened by hand, and every unverified card

Pick one `done-elsewhere` or `obsolete` verdict, or `partly-solved` if there is
none, or one `still-needed` if there are none of those. Open its first
`file:line` on that repo's `main` at the time of reading:

```
gh api "repos/<owner/repo>/contents/<file>?ref=main" --jq .content | base64 -d | sed -n '<line>p'
```

| Card | Verdict | `file:line` | The line on `main`, quoted | Does the reader agree? |
|---|---|---|---|---|
| ⟨FILL⟩ | | | | ⟨FILL: yes / no, and why⟩ |

Then hand-check one card the agent called `still-needed`: read the card and
the cited code, and say whether the reader agrees. This is the check the CEO
asked for on 2026-09-29: whether the verify step still passes cards he would
cancel.

| Card | The agent's summary | The reader's call |
|---|---|---|
| ⟨FILL⟩ | | |

Every `unverified` card, with its reason (from `verify.unverified` and each
row's `reason`):

| Card | Reason |
|---|---|
| ⟨FILL, or "none"⟩ | |

**Pass bar:** one proof opened and quoted, the reader's agreement stated, and
every unverified card named with its reason.

## What is not proven, and why

⟨FILL: anything the readings above could not show, said plainly⟩

## The card's criteria

| Criterion | Result |
|---|---|
| The first scheduled morning run is observed live: named run, the gate's 06:00 PT line, the PT time the proposal landed on the standing card | ⟨FILL⟩ |
| Every `verify` leg with its verdict, the `Verify step:` line quoted, in or out of $8–30 and 4–6 minutes | ⟨FILL⟩ |
| One verdict's proof checked by hand on `main`, and every `unverified` card named with its reason | ⟨FILL⟩ |
| The CEO closes this card after reading the merged record | Open |
