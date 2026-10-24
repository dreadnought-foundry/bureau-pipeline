# PROOF record — DRE-7810: one full week's stop report, read after the change ships, shows the medic moving what it diagnoses and vendor refusals waiting instead of striking — recorded in the repo (epic not read)

**Status: PARTIAL.**

The releases are live, and the week this proof reads is now fixed: Sunday 2026-10-25 to Saturday 2026-10-31 PT. Its report is filed on Sunday 2026-11-01, so every row that reads it waits for that report, stamped `not-before=2026-11-01T09:00-08:00`. The sweep holds this record until then and re-runs it on its own (DRE-7316).

## How this was recorded

- Run: proof run 40094116427, attempt 1 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/40094116427 — dispatched as the card's first proof run.
- Read on 2026-10-24, 05:25 to 05:40 PT. Every time below is Pacific. 2026-10-24 is daylight time (PDT, UTC-7). Pacific daylight time ends on 2026-11-01, so that day's stamp carries `-08:00`.
- Release observed: bureau-pipeline `stable` at `f3a9c80` (`f3a9c801770609db01875735e941e570550243e2`, "Merge pull request #1236 from dreadnought-foundry/agent/DRE-7802-build-asks-wait-reader"). The agent-bureau console release observed is `agent-bureau-console-v1.6.587`, at commit `636acb4` (`636acb41e96fa6d6fdd8f3c857d8a27eac316d27`).
- proof local run: `PROOF_LOCAL_STATUS=none`. No criterion on this card is a `Local screen:` step.
- The epic's id is not in this title. None of the Linear readers the brief allows returns a card's parent: `description`, `state-of`, `dump-comments`, `children-json`, `children-detail` and `find-open`. The card's own text and comments do not name the epic either.

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
- **The straddling report, set aside:** the report for the week ending Saturday 2026-10-24 (Sunday 2026-10-18 to Saturday 2026-10-24). It is due on Sunday 2026-10-25, after both releases, but its window began before both of them. The console release went live on Friday 2026-10-23 at 16:32 PT, and `stable` moved on Saturday 2026-10-24 at 05:20 PT. All but the last 18 hours 40 minutes of that window ran on the old medic and the old build path, so it is not the proof week. It had not been filed when this run read the weeks (2026-10-24 05:34 PT). It is named here so the re-run does not read it.

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

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** The step summary's line reads: `proof identity: github read — app agent-bureau-bot-4, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. It was used for every GitHub read above: pull requests, the `stable` ref, Promote Channel runs and their logs, the console tags and the compares, and both runs of `scripts/proof_release.py`.
  - The one refused write: `GH_TOKEN=$GH_READ_TOKEN gh api --method POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-40094116427 -f sha=f3a9c801770609db01875735e941e570550243e2`.
  - GitHub answered `{"message":"Resource not accessible by integration",…,"status":"403"}` (`gh: Resource not accessible by integration (HTTP 403)`). No ref was created.
- **GitHub, written: `GH_TOKEN`.** Used only to push `agent/DRE-7810-proof-record` and open this record's pull request.
- **Linear: `LINEAR_API_KEY`, the fleet key.** Writes were heartbeats on DRE-7810 and the `agent-actor` marker, nothing else.
  - Reads: `description DRE-7512` (the baseline report), `dump-comments DRE-7810` (looking for the epic's id, not found), and `description DRE-7809` (the same, not found).
  - No card was moved, edited or commented on other than DRE-7810's heartbeats.
  - linear requests: 11 of 40 when this record was committed, summed from the `linear-calls:` lines. Two writes the run still owed came after the commit: the `⏳ 5/5 PR opened` heartbeat (2 requests, as each heartbeat before it took) and the `agent-actor` marker. That brings the total to about 14 of 40.
- **AWS:** aws: none — `proof identity: aws — none, PROOF_ROLE_ARN not provided`. No criterion on this card needs it.
- **The console proof-reader:** not used. The card has no `Sandbox press:` criterion, and this checkout holds no `scripts/console_proof_reader.py`.
- No scratch state was created, and no hosted sign-in was made.

## Criterion table

| Criterion | Result |
| -- | -- |
| The record names, in PT, when bureau-pipeline's `stable` first carried the nine bureau-pipeline cards above and when the agent-bureau release carrying DRE-7809, DRE-7796 and DRE-7797 went live, both read off the release record. It then names the proof week (the first Sunday-to-Saturday PT window starting at or after the later of the two) and the not-before time its report can first be read, and names any straddling report it set aside, with why. | Met. **`stable` first carried all nine:** 2026-10-24 05:20:27 PT, when Promote Channel run 40093292063 moved `stable` to `f3a9c80`. `scripts/proof_release.py check` reads `ready — … stable carries` for all nine merges (#1213, #1219, #1229, #1232, #1234, #1212, #1221, #1227, #1236). **The console release went live** as `agent-bureau-console-v1.6.587` (commit `636acb4`), tagged 2026-10-23 16:32:24 PT, with `deployed: 2026-10-23T23:32:23Z`. It is the first console tag whose compare reads `ahead` for `970ae6e`, and it also carries `e1907ac` and `aa34a25`. **The later of the two** is `stable`, at 2026-10-24 05:20 PT. **The proof week** is Sunday 2026-10-25 to Saturday 2026-10-31 PT, and its report can first be read at `2026-11-01T09:00-08:00`. **Set aside:** the report for the week ending 2026-10-24 (Sun 2026-10-18 to Sat 2026-10-24). It will be filed after both releases, but its window began before them, so all but its last 18 hours 40 minutes ran on the old medic and build path. It was not yet filed at 05:34 PT on 2026-10-24. See "How this was recorded". |
| The record quotes the proof week's diagnosed-then-idle hours from the report's "Frozen weeks" section and states them against the baseline 1,688.5 hours (week ending 2026-10-17), as a difference in hours and as a share. Until the report exists, it is a stamped waiting row in the form above. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: the "Frozen weeks" line for the proof week (expected to be labeled `2026-11-01: diagnosed-then-idle <H> hours, …`), read with `linear_ops.py description` on the report card, stated against the baseline's `2026-10-18: diagnosed-then-idle 1,688.5 hours` (re-read off DRE-7512 at 2026-10-24 05:34 PT) as `<H> − 1,688.5` hours and as `<H> ÷ 1,688.5`. |
| The record quotes the report card's `medic/run-failure-diagnosed` breakdown: by conclusion, moved next by, and what the medic did (re-ran, sent to Planning, named the fix loop, asked a person, no step), the five counts DRE-7797 adds to that breakdown. It also quotes the stops table's next-actor cell for that row. It states how many diagnosed failures the medic moved itself against the baseline, where all 201 were read as `itself` and 21 had not moved at all, and how many it parked in Green Light against the expected 18 to about 79. Until the report exists, it is a stamped waiting row. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: the `medic/run-failure-diagnosed` line quoted in full — by conclusion, moved next by, and DRE-7797's five counts (re-ran, sent to Planning, named the fix loop, asked a person, no step) — and the stops table's next-actor cell for the medic row. These are stated against the baseline's 201 rows, all read as `itself`, with 21 `nothing yet` (re-read off DRE-7512 at 2026-10-24 05:34 PT). The re-ran plus sent-to-Planning count is the number the medic moved itself, expected to be up to 3 re-runs and 2 sent to Planning at last week's mix. The asked-a-person count is set against the expected 18 to about 79. |
| The record quotes, off the same report card, what moved the `fix-path` cards next, as DRE-7797 prints it after `of the fix-loop cases, moved next by`. It states the share that nothing moved against the baseline's 21 of 201 (about 10%), which is the reading of whether the fix loop picked up the cards the medic named to it. Until the report exists, it is a stamped waiting row. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: the text after `of the fix-loop cases, moved next by` on the report card, quoted, with the `nothing yet` count as a share of the fix-loop cases, set against the baseline's 21 of 201 (10.4%). |
| The record reads, off the same report card's description, the week's count of vendor-error deaths that spent a strike and the count that waited, as DRE-7797 prints them, and each `run-failed/api_error/…` breakdown. It states both counts against the baseline of 28 deaths with none read as a wait, and against the expectation above (renewal-window 401s wait, the others stay strikes). It also says how many of the week's deaths carried a 529 or 5xx. Until the report exists, it is a stamped waiting row. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: the struck and waited counts of vendor-error deaths as DRE-7797 prints them, and every `run-failed/api_error/…` line quoted. These are stated against the baseline of 28 deaths, all 401s, 13 inside a renewal window and none read as a wait (re-read off DRE-7512 at 2026-10-24 05:34 PT). The expectation is that renewal-window 401s wait and the others stay strikes. The row also gives the count of 529 and 5xx statuses in the week's breakdowns, against the baseline's none. |
| For one card the report's `medic/run-failure-diagnosed` samples name, the record reads that card's comments with `linear_ops.py dump-comments` and its lane with `state-of`, as the proof-reader identity. It quotes the card's `run-failure-diagnosed` comment and the comment posted after it under the `medic-next-step` act (the receipt DRE-7792 renders, naming class, step and run), with both PT times and the card's lane after the receipt. When that receipt names the `ask-person` step, the record also quotes the three-line question posted before the move to Green Light, as `console_escalation.py` rendered it. Until the report exists, it is a stamped waiting row. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: one card named in the report's `medic/run-failure-diagnosed` samples, read with `linear_ops.py dump-comments` and `state-of`. The row quotes its `run-failure-diagnosed` comment and the `medic-next-step` receipt after it (class, step and run), with both PT times and the card's lane after the receipt. When the receipt names `ask-person`, it also quotes the three-line Finding, Question and Recommendation posted before the move to Green Light. |
| The record's criterion table judges every row above as `Met.`, `Not met.` or `Not observed.` (a waiting row in the stamped form while the report does not exist yet), and a closing "What is not proven, and why" section says what one week cannot show. | Met. Every row above opens with `Met.` or `Not observed.`. Each `Not observed.` row is a waiting row stamped `[not-before=2026-11-01T09:00-08:00]`, read by `proof_record.row_not_before` as 2026-11-01 09:00 PST. The record closes with "What is not proven, and why", below. |

## What is not proven, and why

- **Nothing about the new medic is observed yet.** This run read the release record and the baseline only. Every reading of what the medic and the build path did is waiting for the report for the week ending 2026-10-31.
- **One week is one mix of failures.** The plan's expectations — up to 3 re-runs, 2 sent to Planning and 18 to about 79 questions — are scaled from the baseline week's mix: 178 `bad-code`, 18 `platform-fault`, 3 `transient-infrastructure` and 2 `bad-card`. A week with a different mix can land outside those numbers with the mechanism working, or inside them with it broken. One week cannot tell a trend from noise. The three earlier frozen weeks (1,253.6, 1,512.1 and 1,688.5 hours) moved by hundreds of hours with no change to the medic.
- **Diagnosed-then-idle hours depend on more than the medic.** The hours fall when the fix loop, the sweep and people move the cards, not only when the medic acts. A drop in hours does not by itself show which of them moved a card. That is why the record reads the moved-next-by and what-the-medic-did counts beside the hours.
- **Vendor-error waits depend on the vendor.** A week with no credential renewal window, or no 401 at all, has nothing to wait on. In that week a struck count of zero and a waited count of zero would show nothing about DRE-7795's reader. The baseline's 13 renewal-window 401s are not a promise of 13 next week.
- **A 401 outside a renewal window stays a strike by design.** DRE-7795 reads the pipeline's own renewal 401 by its wording. A strike on an unknown 401 is the expected result, not a failure, and this proof cannot show that an unknown 401 should have waited.
- **One sampled card shows one path.** It shows one class, one step and one receipt. It cannot show that every class's step fires. The other classes are read only as counts on the report.
- **The report is the only witness.** Every number here is the console's own weekly report about the pipeline. This proof checks what the report says, not the runs underneath it. A miscount in DRE-7797's breakdown would be carried here as written.
