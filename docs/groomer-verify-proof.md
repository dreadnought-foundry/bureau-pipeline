# Groomer verify proof — one scheduled 06:00 PT morning, read off live state (DRE-4973)

**Status: OBSERVED 2026-10-05.** The 06:00 PT morning ran. It posted proposal
`d50db9746997` on the standing card at **06:18:07 PT**, with 11 minutes 52
seconds to spare before 06:30 PT. It ran 29 verify legs, at $4.84 and 1 min 8 s of
wall clock. The three observation criteria are met (see the table at the end).
The CEO's close is still open. A proof helper read it off on 2026-10-05: the
read-off script at 08:01–08:02 PT, the Linear comment at 08:07 PT, and the
`main` files at 08:08 PT.

The proof for [DRE-4973](https://linear.app/dreadnoughtfoundry/issue/DRE-4973),
from epic DRE-4967. Every time is Pacific (PDT, UTC−7) and labeled PT. The
shape follows `docs/groomer-two-lists-proof.md`: what was read, when, and what
it said, with the run and comment links.

## Why this morning, and not the "first morning after the wiring card"

The card asks for "the first scheduled morning run after the wiring card
merged". That morning was 2026-09-28 (run 36426267250, every leg unverified
before DRE-5123). The 2026-09-29 morning (run 36572881258) was recorded in
bureau-pipeline #559, and the CEO reopened it at 11:35 PT that day: the verify
step had passed old cards as `still-needed`, so a morning only counts once its
proposal survives a real check. The verify step was rebuilt under DRE-5213
(last merge DRE-5348, #659, 2026-10-02 12:09 PT). The 2026-10-02 morning was
judged not to qualify (DRE-4973 comment, 06:53 PT). The groomer was then off
from the fleet pause (2026-10-02 15:17 PT) until it was turned back on, proposals
only, on 2026-10-04 at 07:31 PT. **2026-10-05 is the first morning after all of
that.** The read-off's first line confirmed that the workflow was `active` when
it was read.

## Before reading: three things already known

1. **The page does not print `file:line`.** `groomer.py`'s `_verdict_line`
   prints only the verdict and the agent's summary. The proofs are in the
   `groom-verdict-*` artifacts and in `proposal-verified.json`. The card's
   title says "file:line proof on every Planning card". Its acceptance
   criteria ask only for every leg's verdict and ONE proof checked by hand.
   This record follows the criteria, takes the proofs from the artifacts, and
   says so. On this morning every Planning card carried at least two proofs in
   its artifact (§2). Printing them on the page would be a separate change.
2. **The verdict vocabulary changed after this card was written.** The card
   names `still-needed`, `done`, `obsolete` and `unverified`. Since DRE-5304
   the agent answers one of `still-needed`, `partly-solved`, `done-elsewhere`,
   `obsolete` or `not-worth-it`, and the runner adds `unverified` and
   `excluded`. The tables below use the current words.
3. **The vericorr question.** On 2026-10-02, DRE-5270 sat on the Planning list
   as `unverified — repo not in config/repo-map.json: vericorr`. That did not
   recur this morning: DRE-5270 is not among the 29 legs.

## How the readings are taken

Read-only, run after the post job had finished:

```
DAY=2026-10-05 bash docs/evidence/DRE-4973/readoff_morning.sh ./morning-2026-10-05
```

The script lists the morning's runs and picks the `schedule` run created at
13:xxZ. It writes every job's start and end in PT. It pulls the gate, groom,
lookup and post logs and one verify log, each per job, because
`gh run view <run> --log` on the whole run fails with "too many API requests".
It downloads `groom-proposal-verified`, `groom-proposal`, every
`groom-verdict-*` and every `groom-lookups-*`, then prints the counts, the
Planning list with each card's verdict and first proofs, and the Cancel list.
It picked run `37314573171` on its own; no `RUN=` was passed.

The PT time the comment landed is read from Linear, not from the log. It is the
`createdAt` of the `🧺 groom-proposal: d50db9746997` comment on DRE-4541,
read once at 08:07 PT. The post job's `commented on DRE-4541` log line,
at 13:18:07.53Z, is the cross-check.

## 1. The run, the gate and the landing time

| | Reading |
|---|---|
| Run | [37314573171](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37314573171), event `schedule`, created 06:08:51 PT, conclusion `success`. Nobody dispatched it. It is the `0 13 * * *` line of the pair, and GitHub fired it 8 min 51 s after the hour |
| Gate `why`, quoted | `it is 06:08 PT and DRE-4541 is open — the groomer runs` |
| Other cron of the pair | [37323782562](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37323782562), event `schedule`, created 07:19:32 PT (the `0 14 * * *` line, fired 19 min 32 s late), conclusion `success`. Its gate says: `it is 07:19 PT, outside the 06:00-06:59 PT grooming hour — this is the other cron of the pair and it does nothing`. Its `schedule` job was skipped, so it read nothing, posted nothing and ran no verify leg. It is the designed no-op, not a second morning |
| Proposal id | `d50db9746997` (cycle 15) |
| Comment landed on DRE-4541 (Linear `createdAt`, PT) | **06:18:07 PT** (`2026-10-05T13:18:07.424Z`). The judgement receipt (`🧠 model-attempt: …`) followed at 06:18:07 PT (`13:18:07.913Z`) |
| Before 06:30 PT? | **Yes**, with 11 min 52 s to spare |

Jobs, PT (from `jobs-pt.tsv`):

```
gate                                     success  06:08:54 PT  06:09:00 PT
schedule / groom                         success  06:09:02 PT  06:14:33 PT
schedule / lookup (EveryBite)            success  06:15:02 PT  06:15:27 PT
schedule / lookup (DeltaSolv)            success  06:15:02 PT  06:15:32 PT
schedule / lookup (dreadnought-foundry)  success  06:15:03 PT  06:16:33 PT
schedule / verify (29 legs)              success  first start 06:16:36 PT, last end 06:17:56 PT (DRE-4874)
schedule / post                          success  06:17:59 PT  06:18:12 PT
(call, drain: skipped — not this trigger)
```

**Pass bar: met.** It was a `schedule` run that nobody dispatched, the gate
line is quoted above, and the comment's `createdAt` of 06:18:07 PT is before
06:30:00 PT.

## 2. Every verify leg, and the cost and wall clock

From `proposal-verified.json` (`verify.counts`, and each row's `verify`) and
each `groom-verdict-*/verdict.json`. "Behind the list" means a card among the
up to ten read after the twenty, with its place in the run's `sequence`.

| # | Card | List | Verdict | First proof (`file:line`) or reason | Repo read |
|---|---|---|---|---|---|
| 1 | DRE-5239 | Planning #1 | `still-needed` | `console/backend/release_brake.py:233` | dreadnought-foundry/agent-bureau |
| 2 | DRE-5202 | Planning #2 | `still-needed` | `console/backend/pipeline_credential.py:399` | dreadnought-foundry/agent-bureau |
| 3 | DRE-4874 | Planning #3 | `partly-solved` | `config/release-pipelines.json:154` | dreadnought-foundry/agent-bureau |
| 4 | DRE-4872 | Planning #4 | `still-needed` | `scripts/release_linear.py:151` | dreadnought-foundry/bureau-pipeline |
| 5 | DRE-5564 | Planning #5 | `still-needed` | `.github/workflows/plan.yml:1805` | dreadnought-foundry/bureau-pipeline |
| 6 | DRE-5654 | Planning #6 | `still-needed` | `infra/scripts/provision-portal.sh:142` | dreadnought-foundry/portico |
| 7 | DRE-4485 | Planning #7 | `still-needed` | `infra/lib/content-bucket.ts:4` | dreadnought-foundry/portico |
| 8 | DRE-5767 | Planning #8 | `still-needed` | `console/backend/tests/webhook_fixtures.py:1` | dreadnought-foundry/agent-bureau |
| 9 | DRE-5761 | Planning #9 | `still-needed` | `console/backend/store.py:784` | dreadnought-foundry/agent-bureau |
| 10 | DRE-5729 | Planning #10 | `still-needed` | `infra/release-console.sh:556` | dreadnought-foundry/agent-bureau |
| 11 | DRE-5662 | Planning #11 | `still-needed` | `docs/demos/export-integrity.md:601` | dreadnought-foundry/portico |
| 12 | DRE-5726 | Planning #12 | `still-needed` | `scripts/runners/start-runners.sh:112` | dreadnought-foundry/agent-bureau |
| 13 | DRE-5721 | Planning #13 | `still-needed` | `scripts/linear-api.py:369` | dreadnought-foundry/agent-bureau |
| 14 | DRE-5720 | Planning #14 | `still-needed` | `scripts/onboard-customer.py:569` | dreadnought-foundry/agent-bureau |
| 15 | DRE-5746 | Planning #15 | `partly-solved` | `scripts/groom_verify_agent.py:116` | dreadnought-foundry/bureau-pipeline |
| 16 | DRE-5745 | Planning #16 | `still-needed` | `scripts/report_fix_result.sh:319` | dreadnought-foundry/bureau-pipeline |
| 17 | DRE-5744 | Planning #17 | `still-needed` | `scripts/ready_lane_writers.py:771` | dreadnought-foundry/bureau-pipeline |
| 18 | DRE-5658 | Planning #18 | `still-needed` | `client/app/e2e/local-portal/signin-and-list.spec.ts:231` | dreadnought-foundry/portico |
| 19 | DRE-5676 | Planning #19 | `still-needed` | `infra/lambda/routes/content.ts:1593` | dreadnought-foundry/portico |
| 20 | DRE-5645 | Planning #20 | `still-needed` | `console/backend/monitors/ci_health.py:84` | dreadnought-foundry/agent-bureau |
| 21 | DRE-4666 | behind the list (#5) | `excluded` | reason: parent epic with 3 open children | none (excluded, not read) |
| 22 | DRE-5727 | behind the list (#12) | `excluded` | reason: moved into Intake on 2026-10-04 | none (excluded, not read) |
| 23 | DRE-5700 | behind the list (#19) | `excluded` | reason: moved into Intake on 2026-10-04 | none (excluded, not read) |
| 24 | DRE-5656 | behind the list (#25) | `excluded` | reason: hand-built | none (excluded, not read) |
| 25 | DRE-5657 | behind the list (#26) | `excluded` | reason: hand-built | none (excluded, not read) |
| 26 | DRE-5650 | behind the list (#27) | `still-needed` | `client/app/e2e/local-portal/signin-and-list.spec.ts:204` | dreadnought-foundry/portico |
| 27 | DRE-5483 | behind the list (#28) | `unverified` | reason: no proof | dreadnought-foundry/bureau-pipeline |
| 28 | DRE-5504 | behind the list (#29) | `still-needed` | `client/frame/portico-app-data.js:183` | dreadnought-foundry/portico |
| 29 | DRE-5260 | behind the list (#30) | `still-needed` | `client/app/src/components/organisms/AnswerPanel.tsx:137` | dreadnought-foundry/portico |

The one Cancel card, DRE-4059, was moved there by the deterministic pre-post
check (layer A), not by a verify leg: `already done — the merged pull request
https://github.com/dreadnought-foundry/bureau-pipeline/pull/415 is for this card`.
A hand read the same morning finds that Cancel wrong. #415 is DRE-4058's PR
(`agent/DRE-4058-one-off-round-bound`, "Closes DRE-4058."), and its body names
DRE-4059 as the sibling still to build. DRE-4968's record carries that finding,
because the pre-post check is that card's subject, not this one's.

Counts, copied from the page's `## Verified against main`:

```
- still-needed: 21
- partly-solved: 2
- done-elsewhere: 0
- obsolete: 0
- not-worth-it: 0
- unverified: 1
- excluded: 5
```

On the Planning list, 18 cards are `still-needed` and 2 are `partly-solved`.
The other 3 `still-needed`, the 1 `unverified` and the 5 `excluded` are behind
the list.

The page's line, quoted: `Verify step: 29 cards, $4.84, 1 min 8 s wall clock`

| Measure | Reading | Epic's prediction | Inside? |
|---|---|---|---|
| Cost | $4.84 (`cost_usd` 4.843437) | $8–30 | **No, below by $3.16** |
| Wall clock | 1 min 8 s (`wall_clock_seconds` 68). The verify jobs span 06:16:36–06:17:56 PT, which is 1 min 20 s | 4–6 min | **No, below by 2 min 52 s** |

This is the third reading under the prediction: $4.81 / 1 min 29 s on 2026-09-29,
$4.39 / 1 min 40 s on 2026-10-02, and $4.84 / 1 min 8 s today. The prediction is
what is off, not the run. The epic's $8–30 and 4–6 minutes overstate a 29-leg
morning by about half on cost and by a factor of three or more on time.

**Pass bar: met.** Every leg is listed with its verdict, the line is quoted,
and both in-or-out statements are made.

## 3. One proof opened by hand, and every unverified card

There was no `done-elsewhere` or `obsolete` verdict, so the hand check takes a
`partly-solved` one: **DRE-4874**. Its first proof was opened on
agent-bureau's `main` at `49484b1eb0` (the head when it was read, 08:08 PT):

```
gh api "repos/dreadnought-foundry/agent-bureau/contents/config/release-pipelines.json?ref=main" \
  -H "Accept: application/vnd.github.raw" | awk 'NR==154'
```

| Card | Verdict | `file:line` | The line on `main`, quoted | Does the reader agree? |
|---|---|---|---|---|
| DRE-4874 | `partly-solved` | `config/release-pipelines.json:154` | `      "name": "agent-bureau-relay-gh",` | **Yes.** The line is the relay-gh pipeline, the part the agent says is done. The file names five pipelines: `agent-bureau-console`, `-website`, `-relay`, `-relay-gh` and `portico-portals`. None is for DeltaSolv, Atlas or agent-bureau-demo, which is the part the agent says remains. "Partly solved" is the right word |

Then a hand check of one card the agent called `still-needed`: **DRE-5239**,
Planning #1. Its proofs were opened in `console/backend/release_brake.py` on
the same `main`:

| Card | The agent's summary | The reader's call |
|---|---|---|
| DRE-5239 | "a console Resume simply deletes the console hold from the set (console_holds), and brake_state then turns any project whose newest train decision is the brake's `held` into a KIND_OUTSIDE hold, with no comparison against the time of the last resume" | **Agree, still needed.** Lines 233–234 (`if write.get("op") == _OP_DELETE:` / `held.pop((kind, target), None)`) drop the hold on Resume. Lines 295–304 then add a `KIND_OUTSIDE` hold for every project whose newest decision is `BRAKE_CODE` (line 298: `if d.code != BRAKE_CODE or not full:`). Nothing compares `d.at` with the time of the resume. The fault the card describes is on `main` as written |

Every `unverified` card, with its reason (from `verify.unverified` and the
verdict's `reason`):

| Card | Reason |
|---|---|
| DRE-5483 | `no proof`. The agent's summary: "This roll-up's problem is the missing button and panel in Portico and Agent Bureau, which are other repos and cannot be seen in bureau-pipeline's main … I cannot tell whether the set is done or still open." It is behind the list, not on it. The page lists it under `Not verified — each stays where the proposal put it:` |

**Pass bar: met.** One proof was opened and quoted, the reader's agreement is
stated, a `still-needed` card was hand-checked, and the one unverified card is
named with its reason.

## What else the readings show

- **Layer A's merged-PR search was unread for all 30 cards it reads.** The
  groom log says why: `merged_prs for DeltaSolv could not be read: the Bureau
  App token's installation cannot see it`, and the same for EveryBite. The
  per-owner lookup legs did read each owner on its own token. Each read 21
  cards: EveryBite 45 requests in 16.6 s, DeltaSolv 45 requests in 18.8 s, and
  dreadnought-foundry 201 requests in 81.1 s, each of a budget of 600. The fold
  line was `folded 3 document(s) — 21 card(s) ok, 0 failed, 3 named no file`.
  This gap is DRE-4968's question and DRE-5746's card (Planning #15, judged
  `partly-solved`). It does not bear on this card's three criteria.
- **DRE-5317's stop did not fire, and should not have.** The `verify` block
  reads `all_lookups_failed: false`, `merged_prs_unread: false`,
  `lookups_failed: []` and `not_posted_why: null`.
- **Linear spent**, as the `linear-budget:` lines print it. Gate: `spent 0 this
  run`. Groom: `2499 → 2481 (spent 18 this run (refilled mid-run) …)`, then
  `2479 → 2441 (spent 38 this run; window resets 07:14 PT; refused after 36
  calls; …)`. Post: `spent 2`, then `spent 1`. This record does not explain the
  groom job's `refused after 36 calls`. The check still moved DRE-4059 to Cancel
  and named all 30 cards on its unread line.

## What is not proven, and why

- **Proofs on the page.** The criteria are met from the artifacts. The page
  itself still prints no `file:line` (see "three things already known", item 1).
  If the title's "file:line proof on every Planning card" is meant literally
  on the page, that needs a change to `_verdict_line`, which is outside this
  record.
- **The 06:30 PT bar under a later cron.** GitHub fired the 13:00Z line 8 min
  51 s late, and the morning still had 11 min 52 s to spare. A delay beyond
  about 20 minutes would miss the bar with no fault in the groomer. This
  morning does not show what happens then.
- **The prediction.** Cost and wall clock are measured, not judged. Whether
  the epic's $8–30 and 4–6 minutes should be restated is the CEO's call.

## The card's criteria

| Criterion | Result |
|---|---|
| The first scheduled morning run is observed live: named run, the gate's 06:00 PT line, the PT time the proposal landed on the standing card | **Met.** Run 37314573171; `it is 06:08 PT and DRE-4541 is open — the groomer runs`; the comment landed at 06:18:07 PT, before 06:30 PT (§1) |
| Every `verify` leg with its verdict, the `Verify step:` line quoted, in or out of $8–30 and 4–6 minutes | **Met.** 29 legs listed. `Verify step: 29 cards, $4.84, 1 min 8 s wall clock`, both below the prediction, by $3.16 and by 2 min 52 s (§2) |
| One verdict's proof checked by hand on `main`, and every `unverified` card named with its reason | **Met.** DRE-4874's `config/release-pipelines.json:154` was opened and quoted, and the reader agrees. DRE-5239 was hand-checked, and the reader agrees. DRE-5483 is unverified with `no proof` (§3) |
| The CEO closes this card after reading the merged record | Open |
