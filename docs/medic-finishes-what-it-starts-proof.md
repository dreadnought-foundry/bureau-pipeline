# PROOF record — DRE-7810: one full week's stop report, read after the change ships, shows the medic moving what it diagnoses and vendor refusals waiting instead of striking — recorded in the repo (epic DRE-6681)

**Status: FAIL.**

The proof week's report exists now. It is DRE-8589, `What broke the agents — week ending 2026-10-31 (PT)`, filed 2026-11-01 05:33 PT. Read against the baseline week, diagnosed-then-idle hours fell from 1,688.5 to 691.9. The medic took a declared step on 95 of its 105 diagnoses, and the report credits it with moving 2 itself instead of all of them. None of the 16 vendor-error deaths spent a strike, and 1 waited. The medic asked a person 10 times, below the plan's 18 to about 79, which was scaled to the baseline week's mix. Five rows are met. The sample-card row is `Not met.` as wording-only: the report card names no sample for the medic row, so the card read is one the report names under another row, found by a Linear comment search.

## How this was recorded

- **This run (the timed re-run):** proof run 41329574902 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41329574902 — dispatched as `timed re-run after not-before 2026-11-01 09:00 PT at 6a6165f`. It read on 2026-11-01 from 09:05 to 09:09 PT, on Pacific standard time (PST, UTC-8). It resumed `agent/DRE-7810-proof-record` at `6a6165f` and merged `main` (`117b5a0`) into it. It amended every row that waited for the report, and the straddling report in row 1.
- **The first run:** proof run 40094116427, attempt 1 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/40094116427. It read on 2026-10-24 from 05:25 to 05:40 PT, on daylight time (PDT, UTC-7). It took the release readings below and the baseline re-read.
- Every time below is Pacific.
- Release observed: bureau-pipeline `stable` at `f3a9c80` (`f3a9c801770609db01875735e941e570550243e2`, "Merge pull request #1236 from dreadnought-foundry/agent/DRE-7802-build-asks-wait-reader"). The agent-bureau console release observed is `agent-bureau-console-v1.6.587`, at commit `636acb4` (`636acb41e96fa6d6fdd8f3c857d8a27eac316d27`).
- **Re-read in this run, as the read token, at 09:08 PT.**
  - `stable` now points at `117b5a0` (type `commit`). `compare/f3a9c80…stable` reads `ahead` by 484.
  - `compare/f9c2a77…f3a9c80` reads `ahead`.
  - Promote Channel run 40093292063 reads `success`, head `f3a9c80`, updated `2026-10-24T12:20:41Z` (05:20:41 PT).
  - `agent-bureau-console-v1.6.587` is tag object `92160ce`. It is tagged `2026-10-23T23:32:24Z`, with the message `sha: 636acb41e` and `deployed: 2026-10-23T23:32:23Z`.
  - `compare/<merge>…agent-bureau-console-v1.6.587` reads `ahead` for `e1907ac`, `aa34a25` and `970ae6e`. `compare/970ae6e…agent-bureau-console-v1.6.586` reads `behind`.
- proof local run: `PROOF_LOCAL_STATUS=none`. No criterion on this card is a `Local screen:` step.
- **The epic is DRE-6681.** It was read in this run with one `linear_ops.gql` query, `issue(id: "DRE-7810") { parent { identifier title } }`. The title begins `[EPIC] bureau-pipeline: the medic finishes what it starts`. The first run had not read it.

### The release record, read

The merges were read with `GH_TOKEN=$GH_READ_TOKEN gh pr list --repo dreadnought-foundry/<repo> --state merged --search "DRE-<n> in:title"`. Then the release reader was run as the read token:

```
GH_TOKEN=$GH_READ_TOKEN python3 scripts/proof_release.py check --repo dreadnought-foundry/bureau-pipeline \
  --merge DRE-7792:1213:ad63912… --merge DRE-7793:1219:459e6db… --merge DRE-7799:1229:141a37f… \
  --merge DRE-7794:1232:c462dae… --merge DRE-7805:1234:f9c2a77… --merge DRE-7795:1212:d389318… \
  --merge DRE-7800:1221:e796604… --merge DRE-7801:1227:1a84cd0… --merge DRE-7802:1236:f3a9c80…
proof-release: ready — pipeline-channel (the whole repository): stable carries #1213 (ad63912) for DRE-7792
proof-release: ready — pipeline-channel (the whole repository): stable carries #1219 (459e6db) for DRE-7793
proof-release: ready — pipeline-channel (the whole repository): stable carries #1229 (141a37f) for DRE-7799
proof-release: ready — pipeline-channel (the whole repository): stable carries #1232 (c462dae) for DRE-7794
proof-release: ready — pipeline-channel (the whole repository): stable carries #1234 (f9c2a77) for DRE-7805
proof-release: ready — pipeline-channel (the whole repository): stable carries #1212 (d389318) for DRE-7795
proof-release: ready — pipeline-channel (the whole repository): stable carries #1221 (e796604) for DRE-7800
proof-release: ready — pipeline-channel (the whole repository): stable carries #1227 (1a84cd0) for DRE-7801
proof-release: ready — pipeline-channel (the whole repository): stable carries #1236 (f3a9c80) for DRE-7802
exit 0

GH_TOKEN=$GH_READ_TOKEN python3 scripts/proof_release.py check --repo dreadnought-foundry/agent-bureau \
  --merge DRE-7809:4111:e1907ac… --merge DRE-7796:4112:aa34a25… --merge DRE-7797:4115:970ae6e…
proof-release: ready — console: newest agent-bureau-console-v1.6.596 carries #4111 (e1907ac) for DRE-7809
proof-release: ready — console: newest agent-bureau-console-v1.6.596 carries #4112 (aa34a25) for DRE-7796
proof-release: ready — console: newest agent-bureau-console-v1.6.596 carries #4115 (970ae6e) for DRE-7797
proof-release: ready — website: untouched — … changed nothing under website/ that it does not ignore
proof-release: ready — relay: untouched — … changed nothing under cloud/relay/ that it does not ignore
proof-release: ready — relay-gh: untouched — … changed nothing under cloud/relay-gh/ that it does not ignore
exit 0
```

| Card | Pull request | Merge commit | Merged (PT) |
| -- | -- | -- | -- |
| DRE-7809 | agent-bureau #4111 | `e1907ac` | 2026-10-23 14:55 |
| DRE-7796 | agent-bureau #4112 | `aa34a25` | 2026-10-23 14:59 |
| DRE-7797 | agent-bureau #4115 | `970ae6e` | 2026-10-23 15:27 |
| DRE-7792 | bureau-pipeline #1213 | `ad63912` | 2026-10-23 15:49 |
| DRE-7795 | bureau-pipeline #1212 | `d389318` | 2026-10-23 16:07 |
| DRE-7793 | bureau-pipeline #1219 | `459e6db` | 2026-10-23 17:18 |
| DRE-7800 | bureau-pipeline #1221 | `e796604` | 2026-10-23 17:45 |
| DRE-7801 | bureau-pipeline #1227 | `1a84cd0` | 2026-10-23 20:08 |
| DRE-7799 | bureau-pipeline #1229 | `141a37f` | 2026-10-23 21:03 |
| DRE-7794 | bureau-pipeline #1232 | `c462dae` | 2026-10-23 22:57 |
| DRE-7805 | bureau-pipeline #1234 | `f9c2a77` | 2026-10-24 01:44 |
| DRE-7802 | bureau-pipeline #1236 | `f3a9c80` | 2026-10-24 04:10 |

**When `stable` first carried all nine.** `stable` is a moving lightweight tag (`gh api repos/dreadnought-foundry/bureau-pipeline/git/ref/tags/stable` → `f3a9c80…`, type `commit`), so the ref has no date of its own. The time it moved is read off the run that moved it: Promote Channel run 40093292063, step "Move the channel". The step's `gh api --method PATCH` answered `{"ref":"refs/tags/stable",…}` at **2026-10-24 05:20:27 PT**, with `REASON: promoting stable to f3a9c801770609db01875735e941e570550243e2 — harness green, strictly ahead.` and `CHANNEL_RECEIPT: pipeline-channel: state=current behind=0 since=none tag=f3a9c80 head=f3a9c80`.

Before that, `stable` stood at `f9c2a77`, which does not carry DRE-7802. Promote Channel run 40089472149 read `pipeline-channel: state=advancing behind=5 … tag=f9c2a77 head=f3a9c80` at 04:20 PT and refused to move: `unit-suite-not-finished`. Run 40091455271 read `stable is already at f9c2a7717c58… — nothing to do.` at 04:49 PT. `f3a9c80` is DRE-7802's own merge commit, so no earlier `stable` head could carry it.

**When the agent-bureau console release carrying DRE-7809, DRE-7796 and DRE-7797 went live.** Each console tag was compared against each merge with `GH_TOKEN=$GH_READ_TOKEN gh api repos/dreadnought-foundry/agent-bureau/compare/<merge>...<tag> --jq .status`, and its date read with `gh api repos/dreadnought-foundry/agent-bureau/git/tags/<tag object> --jq .tagger.date`:

| Tag | Tagged (PT) | Commit | Carries DRE-7797 (`970ae6e`) |
| -- | -- | -- | -- |
| agent-bureau-console-v1.6.585 | 2026-10-23 15:17:16 | `e1907ac` | no (`behind`) |
| agent-bureau-console-v1.6.586 | 2026-10-23 15:54:50 | `04b6ba4` | no (`behind`) |
| **agent-bureau-console-v1.6.587** | **2026-10-23 16:32:24** | `636acb4` | **yes (`ahead`)** |

`agent-bureau-console-v1.6.587` also answers `ahead` for `e1907ac` (DRE-7809) and `aa34a25` (DRE-7796). Its tag message reads `sha: 636acb41e` and `deployed: 2026-10-23T23:32:23Z`, which is 2026-10-23 16:32:23 PT. The console's tag is its deploy record (`record: tag` in agent-bureau's `.github/bureau/release.json`).

### The weeks

- **The later release:** bureau-pipeline `stable`, at 2026-10-24 05:20 PT (a Saturday).
- **The proof week:** Sunday 2026-10-25 00:00 PT to Saturday 2026-10-31 24:00 PT. This is the first Sunday-to-Saturday PT window that starts at or after 2026-10-24 05:20 PT.
- **When its report can first be read:** the report is filed the Sunday after the week, 2026-11-01. The baseline report was filed at 05:43 PT. The record is stamped `not-before=2026-11-01T09:00-08:00`, which is 09:00 PT on the first day of Pacific standard time.
- **The straddling report, set aside:** the report for the week ending Saturday 2026-10-24 (Sunday 2026-10-18 to Saturday 2026-10-24). It is due on Sunday 2026-10-25, after both releases, but its window began before both of them. The console release went live on Friday 2026-10-23 at 16:32 PT, and `stable` moved on Saturday 2026-10-24 at 05:20 PT. All but the last 18 hours 40 minutes of that window ran on the old medic and the old build path, so it is not the proof week. It had not been filed when the first run read the weeks (2026-10-24 05:34 PT). This run found it as **DRE-7960**, `What broke the agents — week ending 2026-10-24 (PT)`, created 2026-10-25 05:39:09 PT and closed (`Done`) 2026-10-25 07:47:20 PT. Its description was not read. The proof week's report carries its frozen-week line, `2026-10-25: diagnosed-then-idle 1,198.6 hours, vendor-error deaths 8`, which no row below judges.
- **The proof week's report:** **DRE-8589**, `What broke the agents — week ending 2026-10-31 (PT)`. It was created 2026-11-01 05:33:36 PT, and the card was closed (`Done`) at 08:10:27 PT, after the CEO's console picks on it. Both cards were found by one `linear_ops.gql` query, as the fleet key, at 09:06:07 PT on 2026-11-01. The query asked team DRE's issues, archived ones included, for titles starting `What broke the agents — week ending 2026-1`. Before that, `linear_ops.py find-open` on each exact title printed nothing, because `find-open` skips closed cards and both were closed. Both titles are in the form agent-bureau's `weekly_agent_report_card.REPORT_TITLE` gives, `What broke the agents — week ending {saturday} (PT)`. They were read off agent-bureau's `console/backend/weekly_agent_report_card.py` as the read token.

### The baseline, re-read

`python3 .bureau-pipeline/scripts/linear_ops.py description DRE-7512` was run at 2026-10-24 05:34 PT, using the run's Linear key (the fleet key, the only Linear identity the run holds). It returned the lines the card's baseline quotes:

```
## Frozen weeks
2026-10-18: diagnosed-then-idle 1,688.5 hours, vendor-error deaths 28
| medic | run-failure-diagnosed | 201 | 0 | $0.00 | 1,688.5 | 0.0 | 0 | itself | falling |
medic/run-failure-diagnosed: 201 — by conclusion: bad-code 178, platform-fault 18, transient-infrastructure 3, bad-card 2; moved next by: a sweep 31, a person 6, a merge 143, nothing yet 21.
run-failed/api_error/Bash/nonzero_exit: 11 — 8 inside a credential renewal window, 3 with a vendor status (401: 11).
run-failed/api_error/Bash/success: 8 — 1 inside a credential renewal window, 7 with a vendor status (401: 8).
run-failed/api_error/-/success: 7 — 3 inside a credential renewal window, 4 with a vendor status (401: 7).
run-failed/api_error/Bash/tool_use_error: 2 — 1 inside a credential renewal window, 1 with a vendor status (401: 2).
```

The "Frozen weeks" section labels the baseline week by the report's filing date, 2026-10-18, not by its Saturday, 2026-10-17. The proof week's line is expected to read `2026-11-01: …`. Across the four `api_error` breakdowns, 28 deaths are all 401s, and 13 fell inside a renewal window.

### The proof week's report, read

`python3 .bureau-pipeline/scripts/linear_ops.py description DRE-8589` was run at 2026-11-01 09:06:10 PT, as the fleet key. These are the lines the rows below quote, as the card carries them:

```
## Frozen weeks
2026-11-01: diagnosed-then-idle 691.9 hours, vendor-error deaths 16
2026-10-25: diagnosed-then-idle 1,198.6 hours, vendor-error deaths 8
2026-10-18: diagnosed-then-idle 1,688.5 hours, vendor-error deaths 28
2026-10-11: diagnosed-then-idle 1,512.1 hours, vendor-error deaths 54
2026-10-04: diagnosed-then-idle 1,253.6 hours, vendor-error deaths 59 (frozen: board cleanup, the freeze on new epics and the drain-first restart — cards stood idle because nobody was allowed to move them)

| medic | run-failure-diagnosed | 105 | 0 | $0.00 | 691.9 | 0.0 | 0 | itself 2 · a merge 66 · a sweep 25 · a person 6 · nothing yet 6 | flat |

medic/run-failure-diagnosed: 105 — by conclusion: bad-code 79, transient-infrastructure 7, bad-card 4, platform-fault 4, unreadable 11; moved next by: a sweep 26, a person 6, a merge 67, nothing yet 6; the medic: re-ran 2, sent to Planning 0, named the fix loop 83, asked a person 10, no step 10; of the fix-loop cases, moved next by a sweep 14, a person 5, a merge 62, nothing yet 2.

run-failed/api_error/Bash/nonzero_exit: 6 — 2 inside a credential renewal window, 4 with a vendor status (401: 6); 0 spent a strike, 0 waited, 6 not a build run.
run-failed/api_error/Bash/success: 5 — 2 inside a credential renewal window, 3 with a vendor status (401: 5); 0 spent a strike, 0 waited, 5 not a build run.
run-failed/api_error/-/success: 5 — 1 inside a credential renewal window, 4 with a vendor status (401: 5); 0 spent a strike, 1 waited, 4 not a build run.
Vendor-error strikes: 0 of 16 vendor-error deaths spent a strike; 1 waited for the vendor or the renewal.
```

The report's own week line reads `Sunday 2026-10-25 through Saturday 2026-10-31 (PT).`, the proof week. The card's description names no sampled card for the `medic/run-failure-diagnosed` row. Its card lists appear only under "Stopped for you but did not need you", for `green-light/unreceipted`, `green-light/agent-question` and `green-light/planning-escalation`, and its run lists only under "Failed runs by pattern". agent-bureau's `console/backend/agent_report_compose.py`, read as the read token, renders a sample's card only into those call-outs. The samples behind the medic row live in the stored report document, in the console's Record, which this run cannot read (`aws: none`). `dump-comments DRE-8589` (09:07:12 PT) returned four comments: three signed console answers picking fix-1, fix-2 and fix-3, and a closing note. None names a sample.

### The sample card, read

There was no medic-row sample to take, so the card was chosen another way. One `linear_ops.gql` query, as the fleet key, at 09:07:19 PT, asked for comments containing `medic-next-step: class=` created from 2026-10-25 00:00 PT up to 2026-11-01 00:00 PT. It returned its first 100 matches, with more not fetched, so it is used only to choose a card and never as a count. Among them was `🩺 medic-next-step: class=platform-fault step=ask-person run=41217113274` on **DRE-8507**. The report card itself names DRE-8507, linked, among the 20 cards of `green-light/agent-question`.

- `python3 .bureau-pipeline/scripts/linear_ops.py dump-comments DRE-8507` (09:07:27 PT) returned 30 comments.
- `python3 .bureau-pipeline/scripts/linear_ops.py state-of DRE-8507` (09:07:27 PT) printed `Done`.
- `dump-comments` carries no times. So one `linear_ops.gql` query (09:07:36 PT) read the card's comments with `createdAt` and its state history. The lines below are its times, converted to PT.

```
2026-10-31 09:12:17 PDT  comment  Failed again at https://github.com/dreadnought-foundry/agent-bureau/actions/runs/41217113274 — …
2026-10-31 09:12:18 PDT  comment  🙋 The medic diagnosed a failure only a person can settle, and asks: …
2026-10-31 09:12:19 PDT  lane     In Review -> Green Light
2026-10-31 09:12:20 PDT  comment  🩺 medic-next-step: class=platform-fault step=ask-person run=41217113274
2026-10-31 17:53:45 PDT  lane     Green Light -> In Progress
2026-10-31 18:39:09 PDT  lane     In Progress -> In Review
2026-10-31 18:39:40 PDT  lane     In Review -> Done
```

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** This run's step summary line reads: `proof identity: github read — app agent-bureau-bot-4, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`.
  - The first run used it for every GitHub read in "The release record, read": pull requests, the `stable` ref, Promote Channel runs and their logs, the console tags and the compares, and both runs of `scripts/proof_release.py`.
  - This run used it to re-read the `stable` ref, two compares, Promote Channel run 40093292063, the console tag and its four compares. It also read agent-bureau's `console/backend/weekly_agent_report_card.py`, `weekly_agent_report.py`, `agent_report_compose.py` and `weekly_stops.py`, through code search and the contents API.
  - This run's refused write: `GH_TOKEN=$GH_READ_TOKEN gh api --method POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-41329574902 -f sha=117b5a0ee32d6684e7c62e40834287ad7ad93ac1`, at 09:08:22 PT.
  - GitHub answered `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}` (`gh: Resource not accessible by integration (HTTP 403)`). No ref was created.
  - The first run's probe, `…/refs -f ref=refs/heads/proof-write-probe-40094116427 …`, got the same 403.
- **GitHub, written: `GH_TOKEN`.** Used only to push `agent/DRE-7810-proof-record`. This run opens no pull request: the workflow decides it.
- **Linear: `LINEAR_API_KEY`, the fleet key.** Its only writes were heartbeats on DRE-7810 and the `agent-actor` marker.
  - This run's reads were:
    - `find-open` on the two exact report titles;
    - one `linear_ops.gql` title-prefix query;
    - `description DRE-8589`;
    - `dump-comments DRE-8589`;
    - one `linear_ops.gql` comment search;
    - `dump-comments DRE-8507` and `state-of DRE-8507`;
    - one `linear_ops.gql` read of DRE-8507's comment times and state history;
    - one `linear_ops.gql` read of DRE-7810's parent.
  - The first run's reads were `description DRE-7512`, `dump-comments DRE-7810` and `description DRE-7809`.
  - No card was moved, edited or commented on other than DRE-7810's heartbeats.
  - linear requests: 17 of 40 when this record was committed, summed from this run's `linear-calls:` lines. Heartbeats 1/5, 2/5 and 3/5 took 2 requests each. The ten reads above took 1 each. The local name check (`name_redact.py guard`) took 1. The writes still owed after the commit are the 4/5 and 5/5 heartbeats and the `agent-actor` marker, about 6 more. That brings the total to about 23 of 40.
- **AWS:** aws: none — `proof identity: aws — none, PROOF_ROLE_ARN not provided`. The report's stored document, which holds the medic row's samples, would need it, and this record reads round it (row 6).
- **The console proof-reader:** not used. The card has no `Sandbox press:` criterion, and this checkout holds no `scripts/console_proof_reader.py`.
- No scratch state was created, and no hosted sign-in was made.

## Criterion table

| Criterion | Result |
| -- | -- |
| The record names, in PT, when bureau-pipeline's `stable` first carried the nine bureau-pipeline cards above and when the agent-bureau release carrying DRE-7809, DRE-7796 and DRE-7797 went live, both read off the release record. It then names the proof week (the first Sunday-to-Saturday PT window starting at or after the later of the two) and the not-before time its report can first be read, and names any straddling report it set aside, with why. | Met. **`stable` first carried all nine:** 2026-10-24 05:20:27 PT, when Promote Channel run 40093292063 moved `stable` to `f3a9c80`. `scripts/proof_release.py check` reads `ready — … stable carries` for all nine merges (#1213, #1219, #1229, #1232, #1234, #1212, #1221, #1227, #1236). This run re-read that run as `success` at head `f3a9c80`. **The console release went live** as `agent-bureau-console-v1.6.587` (commit `636acb4`), tagged 2026-10-23 16:32:24 PT, with `deployed: 2026-10-23T23:32:23Z`. It is the first console tag whose compare reads `ahead` for `970ae6e`, and it also carries `e1907ac` and `aa34a25`. This run re-read all three compares as `ahead`, and `v1.6.586` as `behind` for `970ae6e`. **The later of the two** is `stable`, at 2026-10-24 05:20 PT. **The proof week** is Sunday 2026-10-25 to Saturday 2026-10-31 PT, and its report can first be read at `2026-11-01T09:00-08:00`. That report is DRE-8589, filed 2026-11-01 05:33 PT, and its week line reads `Sunday 2026-10-25 through Saturday 2026-10-31 (PT).`. **Set aside:** DRE-7960, the report for the week ending 2026-10-24 (Sun 2026-10-18 to Sat 2026-10-24), filed 2026-10-25 05:39 PT. It was filed after both releases, but its window began before them. All but its last 18 hours 40 minutes ran on the old medic and build path. See "How this was recorded". |
| The record quotes the proof week's diagnosed-then-idle hours from the report's "Frozen weeks" section and states them against the baseline 1,688.5 hours (week ending 2026-10-17), as a difference in hours and as a share. Until the report exists, it is a stamped waiting row in the form above. | Met. DRE-8589's "Frozen weeks" section (`linear_ops.py description DRE-8589`, 2026-11-01 09:06 PT) reads `2026-11-01: diagnosed-then-idle 691.9 hours, vendor-error deaths 16`. The baseline's line, re-read off DRE-7512 and carried again in DRE-8589's own section, is `2026-10-18: diagnosed-then-idle 1,688.5 hours, vendor-error deaths 28`. The difference is 691.9 − 1,688.5 = **−996.6 hours**. The proof week is **41.0%** of the baseline (691.9 ÷ 1,688.5), a drop of 59.0%. |
| The record quotes the report card's `medic/run-failure-diagnosed` breakdown: by conclusion, moved next by, and what the medic did (re-ran, sent to Planning, named the fix loop, asked a person, no step), the five counts DRE-7797 adds to that breakdown. It also quotes the stops table's next-actor cell for that row. It states how many diagnosed failures the medic moved itself against the baseline, where all 201 were read as `itself` and 21 had not moved at all, and how many it parked in Green Light against the expected 18 to about 79. Until the report exists, it is a stamped waiting row. | Met. **The breakdown** (DRE-8589, 09:06 PT): `medic/run-failure-diagnosed: 105 — by conclusion: bad-code 79, transient-infrastructure 7, bad-card 4, platform-fault 4, unreadable 11; moved next by: a sweep 26, a person 6, a merge 67, nothing yet 6; the medic: re-ran 2, sent to Planning 0, named the fix loop 83, asked a person 10, no step 10; …`. **The stops table's next-actor cell** for the row is `itself 2 · a merge 66 · a sweep 25 · a person 6 · nothing yet 6`. **Moved itself:** 2 of 105 (1.9%): re-ran 2 plus sent to Planning 0. The cell credits `itself` with exactly those 2. The baseline credited `itself` with all 201, while the medic then only re-ran the 3 it read as transient. **Not moved at all:** `nothing yet 6` of 105 (5.7%), against the baseline's 21 of 201 (10.4%). **Parked in Green Light:** `asked a person 10`. That is **below** the plan's expected 18 to about 79, by 8 under its floor. The plan scaled that range to the baseline week's mix, whose floor was its 18 `platform-fault` diagnoses. This week had 105 diagnoses against 201, and 4 `platform-fault` against 18. At this week's mix, with the declared steps (`config/medic-next-steps.json`), the `platform-fault` 4 and `unreadable` 11 alone would give at least 15 asks. The 10 is below that too. "What is not proven" says why the two readings need not agree. Of the 105, the medic took a declared step on 95, and 10 read `no step`. |
| The record quotes, off the same report card, what moved the `fix-path` cards next, as DRE-7797 prints it after `of the fix-loop cases, moved next by`. It states the share that nothing moved against the baseline's 21 of 201 (about 10%), which is the reading of whether the fix loop picked up the cards the medic named to it. Until the report exists, it is a stamped waiting row. | Met. DRE-8589 (09:06 PT) prints `of the fix-loop cases, moved next by a sweep 14, a person 5, a merge 62, nothing yet 2.` That is 83 cases, the `named the fix loop 83` of row 3. Nothing moved **2 of 83 (2.4%)**, against the baseline's 21 of 201 (10.4%), a share about a quarter of the baseline's. 62 of the 83 (74.7%) were moved next by a merge. |
| The record reads, off the same report card's description, the week's count of vendor-error deaths that spent a strike and the count that waited, as DRE-7797 prints them, and each `run-failed/api_error/…` breakdown. It states both counts against the baseline of 28 deaths with none read as a wait, and against the expectation above (renewal-window 401s wait, the others stay strikes). It also says how many of the week's deaths carried a 529 or 5xx. Until the report exists, it is a stamped waiting row. | Met. DRE-8589 (09:06 PT) prints `Vendor-error strikes: 0 of 16 vendor-error deaths spent a strike; 1 waited for the vendor or the renewal.` It prints three breakdowns: `run-failed/api_error/Bash/nonzero_exit: 6 — 2 inside a credential renewal window, 4 with a vendor status (401: 6); 0 spent a strike, 0 waited, 6 not a build run.`, `run-failed/api_error/Bash/success: 5 — 2 inside a credential renewal window, 3 with a vendor status (401: 5); 0 spent a strike, 0 waited, 5 not a build run.` and `run-failed/api_error/-/success: 5 — 1 inside a credential renewal window, 4 with a vendor status (401: 5); 0 spent a strike, 1 waited, 4 not a build run.` **Against the baseline:** 16 deaths against 28. **0 struck, 1 waited**, where the baseline read none as a wait. **Against the expectation:** 15 of the 16 were not build runs, so neither a strike nor a wait applied to them. The one build-run death waited, and no build-run death was struck. 5 of the 16 fell inside a renewal window. The card does not say whether the one that waited is one of them. No build-run 401 outside a window occurred, so "the others stay strikes" had nothing to act on this week. **529 or 5xx:** none. All 16 statuses are 401 (6 + 5 + 5), and no other `api_error` breakdown appears on the card. |
| For one card the report's `medic/run-failure-diagnosed` samples name, the record reads that card's comments with `linear_ops.py dump-comments` and its lane with `state-of`, as the proof-reader identity. It quotes the card's `run-failure-diagnosed` comment and the comment posted after it under the `medic-next-step` act (the receipt DRE-7792 renders, naming class, step and run), with both PT times and the card's lane after the receipt. When that receipt names the `ask-person` step, the record also quotes the three-line question posted before the move to Green Light, as `console_escalation.py` rendered it. Until the report exists, it is a stamped waiting row. | Not met. wording-only: seen “🩺 medic-next-step: class=platform-fault step=ask-person run=41217113274” — differs: the card is DRE-8507, which the report card names under `green-light/agent-question`, not among `medic/run-failure-diagnosed` samples. The report card renders no sample for that row, and its stored samples need AWS, which this run does not hold (`aws: none`). The card was found by a Linear comment search instead. Its PT times and its lane after the receipt were read off one `linear_ops.gql` query of its comments and state history, since `dump-comments` carries no times and `state-of` now reads `Done`. **What was seen** (`dump-comments DRE-8507`, 09:07 PT): the `run-failure-diagnosed` comment, posted **2026-10-31 09:12:17 PT**, opens `Failed again at https://github.com/dreadnought-foundry/agent-bureau/actions/runs/41217113274 — the behavior check on DRE-8507 never ran, and the code on the branch was not at fault.` and closes `📎 pipeline-act: run-failure-diagnosed · kind: hold · state: unchanged · next: operator · discharges: nothing · subscriber: reconcile.yml · tag: medic-diagnosis`. The question was posted at **09:12:18 PT**, before the move. It reads `🙋 The medic diagnosed a failure only a person can settle, and asks:` then `🔎 Finding: The final behavior check on this card could not run because our organization has hit its monthly Claude spend limit, so nothing was checked and the code is not at fault.` / `❓ Question: Can the operator who manages our Claude account raise the monthly spend limit, or confirm that it resets today?` / `💡 Recommendation: Raise the limit now, then re-run the check on this card. — Every Claude-powered agent in the pipeline will be turned away the same way until the limit is lifted, so waiting only delays this card and others.` The lane moved `In Review -> Green Light` at **09:12:19 PT**. The receipt was posted at **09:12:20 PT**: `🩺 medic-next-step: class=platform-fault step=ask-person run=41217113274`, then `… So it moved the card to Green Light to ask a person what happens next. …` and `📎 pipeline-act: medic-next-step · kind: recovery · …`. **The lane after the receipt** was `Green Light`, until `Green Light -> In Progress` at 17:53:45 PT. `state-of DRE-8507` reads `Done` now (since 18:39:40 PT). |
| The record's criterion table judges every row above as `Met.`, `Not met.` or `Not observed.` (a waiting row in the stamped form while the report does not exist yet), and a closing "What is not proven, and why" section says what one week cannot show. | Met. Every row above opens with `Met.` or `Not met.`. No row waits any more, because the report exists (DRE-8589). The record closes with "What is not proven, and why", below. |

## What is not proven, and why

- **One week is one mix of failures.** This week had 105 diagnoses where the baseline had 201: 79 `bad-code` against 178, and 4 `platform-fault` against 18. The plan's expectations were scaled to the baseline's mix. A lower asked-a-person count can come from a lighter week as much as from the medic. One week cannot tell a trend from noise. The frozen weeks before this one moved by hundreds of hours with no change to the medic: 1,253.6, 1,512.1, 1,688.5 and 1,198.6 hours.
- **The asked-a-person count is below the plan's range, and this record does not explain it.** The report's conclusions are the console's own reading of each diagnosis (`by conclusion`). The medic's step follows the medic's own class, which its receipt names. The two can differ. The comment search behind row 6 shows that: among its first 100 receipts, only 2 name `class=platform-fault` and 3 name `class=unreadable`, where the console read 4 and 11. The 10 `no step` diagnoses — no receipt found after the diagnosis — are also unexplained here. Some steps were refused rather than taken. The same search shows receipts ending `moved=no`, such as a `class=bad-card step=send-to-planning` receipt on DRE-8271, beside the report's `sent to Planning 0`. One week's report cannot say which of these accounts for the gap.
- **Diagnosed-then-idle hours depend on more than the medic.** The hours fall when the fix loop, the sweep and people move the cards, not only when the medic acts. The week also had about half as many diagnoses. So the 59% drop does not by itself show which of them moved a card. The moved-next-by and what-the-medic-did counts are read beside the hours for that reason.
- **The strike-or-wait reader saw one build-run death.** 15 of the 16 vendor-error deaths were not build runs, so no strike or wait applied to them. A single wait, and no strike, is too few to show that every renewal-window 401 waits. No build-run 401 fell outside a renewal window, so this week cannot show that such a 401 stays a strike, as DRE-7795 intends.
- **One sampled card shows one path.** DRE-8507 shows the `platform-fault` → `ask-person` path end to end: the diagnosis, the three-line question, the move to Green Light and the receipt. It shows nothing about `requeue-once`, `send-to-planning` or `fix-path` beyond the report's counts. It was not drawn from the medic row's samples.
- **The report is the only witness.** Every count here is the console's own weekly report about the pipeline. This proof checks what the report says, not the runs underneath it. A miscount in DRE-7797's breakdown would be carried here as written. The one card read directly agrees with the report: it shows the step the report counts.
