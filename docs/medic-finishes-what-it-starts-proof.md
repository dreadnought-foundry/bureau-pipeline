# PROOF record — DRE-7810: one full week's stop report, read after the change ships, shows the medic moving what it diagnoses and vendor refusals waiting instead of striking — recorded in the repo (epic not read)

**Status: PARTIAL.**

The release half is observed: both releases are live, and the proof week is Sunday 2026-10-25 to Saturday 2026-10-31 PT. The report half is not. At 09:10 PT on 2026-11-01, after the 09:00 PT not-before time, no reader this run holds could return the report card for the week ending 2026-10-31, so the five rows that read it are `Not observed.` with the cause written out.

## How this was recorded

- Run: shadow proof run 41329576913, attempt 1 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41329576913 — dispatched as a timed re-run after not-before 2026-11-01 09:00 PT. It is a shadow run (DRE-7286): it observes and writes this file only, makes no sandbox press and posts nothing to Linear.
- Read on 2026-11-01, 09:05 to 09:15 PT. Every time below is Pacific. Pacific daylight time ended on 2026-11-01, so 2026-11-01 is PST (UTC-8) and the dates before it are PDT (UTC-7).
- Commit observed in the checkout: `117b5a0` on bureau-pipeline `main`. bureau-pipeline `stable` stands at the same commit, `117b5a0ee32d6684e7c62e40834287ad7ad93ac1`.
- proof local run: `PROOF_LOCAL_STATUS=none`. No criterion on this card is a `Local screen:` step.
- The epic's id is not in this title and none of the Linear readers the brief allows returns a card's parent, so the title reads "epic not read".

### The release record, read

The merges were found with `GH_TOKEN=$GH_READ_TOKEN gh api -X GET search/issues -f q="repo:<repo> is:pr is:merged DRE-<n> in:title"`. Then the release reader was run as the read token on a merge of each repo:

```
GH_TOKEN=$GH_READ_TOKEN python3 .bureau-pipeline/scripts/proof_release.py check --repo dreadnought-foundry/bureau-pipeline \
  --merge DRE-7802:1236:f3a9c801770609db01875735e941e570550243e2 --merge DRE-7792:1213:ad63912742fff694dc704da30b5df30ae7305786
proof-release: ready — pipeline-channel (the whole repository): stable carries #1236 (f3a9c80) for DRE-7802
proof-release: ready — pipeline-channel (the whole repository): stable carries #1213 (ad63912) for DRE-7792
```

| Card | Pull request | Merge commit | Merged (PT) | First `stable` deployment carrying it (PT) |
| -- | -- | -- | -- | -- |
| DRE-7795 | bureau-pipeline #1212 | `d389318` | 2026-10-23 16:07 | 2026-10-23 17:16 (`d389318`) |
| DRE-7792 | bureau-pipeline #1213 | `ad63912` | 2026-10-23 15:49 | 2026-10-23 17:03 (`ad63912`) |
| DRE-7793 | bureau-pipeline #1219 | `459e6db` | 2026-10-23 17:18 | 2026-10-23 18:35 (`459e6db`) |
| DRE-7800 | bureau-pipeline #1221 | `e796604` | 2026-10-23 17:45 | 2026-10-23 18:54 (`e796604`) |
| DRE-7801 | bureau-pipeline #1227 | `1a84cd0` | 2026-10-23 20:08 | 2026-10-23 21:18 (`1a84cd0`) |
| DRE-7799 | bureau-pipeline #1229 | `141a37f` | 2026-10-23 21:03 | 2026-10-23 23:57 (`9fafed4`) |
| DRE-7794 | bureau-pipeline #1232 | `c462dae` | 2026-10-23 22:57 | 2026-10-24 02:54 (`f9c2a77`) |
| DRE-7805 | bureau-pipeline #1234 | `f9c2a77` | 2026-10-24 01:44 | 2026-10-24 02:54 (`f9c2a77`) |
| DRE-7802 | bureau-pipeline #1236 | `f3a9c80` | 2026-10-24 04:10 | **2026-10-24 05:20 (`f3a9c80`)** |
| DRE-7809 | agent-bureau #4111 | `e1907ac` | 2026-10-23 14:55 | see the console table below |
| DRE-7796 | agent-bureau #4112 | `aa34a25` | 2026-10-23 14:59 | see the console table below |
| DRE-7797 | agent-bureau #4115 | `970ae6e` | 2026-10-23 15:27 | see the console table below |

How the `stable` column was read: `stable` is a moving lightweight tag, so the ref has no date of its own. Bureau-pipeline records each move as a `release` deployment in the `pipeline-channel` environment (`GH_TOKEN=$GH_READ_TOKEN gh api "repos/dreadnought-foundry/bureau-pipeline/deployments?environment=pipeline-channel"`), and each deployment's `success` status carries the time. The deployment for `f3a9c80` (id 7298508520) has its `success` status at 2026-10-24T12:20:27Z, which is **2026-10-24 05:20:27 PT**. `f3a9c80` is DRE-7802's own merge commit, so no earlier head could carry it, and DRE-7802 merged last. Two merges have no deployment of their own: `141a37f` (DRE-7799) is carried by the deployment at `9fafed4` (compare answers `behind`, meaning the merge is an ancestor of the deployed commit), and `c462dae` (DRE-7794) answers `ahead` of `9fafed4` and `behind` the next deployment, `f9c2a77`. So the nine cards were all carried by `stable` for the first time at **2026-10-24 05:20:27 PT**.

The per-card deployment times above for the first seven rows are rounded to the minute from the deployments' UTC `success` statuses; only the last row's 05:20:27 PT is the time that decides the proof week.

**When the agent-bureau console release carrying DRE-7809, DRE-7796 and DRE-7797 went live.** Each console tag was compared against each merge with `GH_TOKEN=$GH_READ_TOKEN gh api repos/dreadnought-foundry/agent-bureau/compare/<tag>...<merge> --jq .status`, and its time read with `gh api repos/dreadnought-foundry/agent-bureau/git/tags/<tag object> --jq .tagger.date`:

| Tag | Tagged (PT) | Carries DRE-7809 (`e1907ac`) | DRE-7796 (`aa34a25`) | DRE-7797 (`970ae6e`) |
| -- | -- | -- | -- | -- |
| agent-bureau-console-v1.6.585 | 2026-10-23 15:17:16 | `identical` | `ahead` — not carried | `ahead` — not carried |
| agent-bureau-console-v1.6.586 | 2026-10-23 15:54:50 | `behind` — carried | `behind` — carried | `ahead` — not carried |
| **agent-bureau-console-v1.6.587** | **2026-10-23 16:32:24** | `behind` — carried | `behind` — carried | `behind` — carried |

(`behind` here is the compare of the tag against the merge: the merge is an ancestor of the tag.) `agent-bureau-console-v1.6.587` is the first tag carrying all three. Tags 587 to 593 all answer `behind` for `970ae6e`, so none of them carries it less than 587 does.

### The weeks

- **The later release:** bureau-pipeline `stable`, at 2026-10-24 05:20 PT, a Saturday. The console release was earlier, 2026-10-23 16:32 PT.
- **The proof week:** Sunday 2026-10-25 00:00 PT to Saturday 2026-10-31 24:00 PT, the first Sunday-to-Saturday PT window that starts at or after 2026-10-24 05:20 PT.
- **When its report can first be read:** the report is filed the Sunday after the week, 2026-11-01. The baseline's was filed at 05:43 PT, and the console's weekly starter files from Sunday 05:30 PT. The not-before time is `2026-11-01T09:00-08:00`.
- **The straddling report, set aside:** the report for the week ending Saturday 2026-10-24 (Sunday 2026-10-18 to Saturday 2026-10-24), filed Sunday 2026-10-25. Its window began before both releases. The console release went live on Friday 2026-10-23 at 16:32 PT, and `stable` moved on Saturday 2026-10-24 at 05:20 PT, so all of that window but its last 18 hours 40 minutes ran on the old medic and the old build path. It is not the proof week, and this record reads nothing from it.

### The baseline, re-read

`python3 .bureau-pipeline/scripts/linear_ops.py description DRE-7512` was run at 2026-11-01 09:10 PT. It returned the lines the card quotes:

```
## Frozen weeks
2026-10-18: diagnosed-then-idle 1,688.5 hours, vendor-error deaths 28
2026-10-11: diagnosed-then-idle 1,512.1 hours, vendor-error deaths 54
2026-10-04: diagnosed-then-idle 1,253.6 hours, vendor-error deaths 59 (frozen)
| medic | run-failure-diagnosed | 201 | 0 | $0.00 | 1,688.5 | 0.0 | 0 | itself | falling |
medic/run-failure-diagnosed: 201 — by conclusion: bad-code 178, platform-fault 18, transient-infrastructure 3, bad-card 2; moved next by: a sweep 31, a person 6, a merge 143, nothing yet 21.
run-failed/api_error/Bash/nonzero_exit: 11 — 8 inside a credential renewal window, 3 with a vendor status (401: 11).
run-failed/api_error/Bash/success: 8 — 1 inside a credential renewal window, 7 with a vendor status (401: 8).
run-failed/api_error/-/success: 7 — 3 inside a credential renewal window, 4 with a vendor status (401: 7).
run-failed/api_error/Bash/tool_use_error: 2 — 1 inside a credential renewal window, 1 with a vendor status (401: 2).
```

The "Frozen weeks" section labels the baseline week by its report's filing date, 2026-10-18, not by its Saturday, 2026-10-17. The proof week's line is expected to read `2026-11-01: …`.

### Looking for the proof week's report card

The report's title for the proof week is, per `weekly_agent_report_card.report_title`, `What broke the agents — week ending 2026-10-31 (PT)`. These reads were made at 09:05 to 09:12 PT, all as the run's Linear key:

- `python3 .bureau-pipeline/scripts/linear_ops.py find-open "What broke the agents — week ending 2026-10-31 (PT)"` printed no identifier.
- The same for the straddling week, `… week ending 2026-10-24 (PT)`: no identifier.
- `python3 .bureau-pipeline/scripts/linear_ops.py find-open-prefix "What broke the agents"` printed no identifier. So no report card of any week is open.
- `find-open` and `find-open-prefix` skip cards in a completed or canceled state. The report card is filed in Green Light, to be read and closed, and the baseline, DRE-7512, is `Done` (`state-of` answered `Done`). None of the readers the brief allows (`description`, `state-of`, `dump-comments`, `children-json`, `children-detail`, `find-open`, `spoken_thread.py thread`) finds a card by title in a closed state, and the report's identifier is not named anywhere this run could read.
- Ten neighbouring identifiers, DRE-8571 to DRE-8579 and DRE-8581, were read with `description` to see whether the card sat near the other cards created that morning. All ten are other cards (a studio-host epic's children and a design card), none a weekly report. That guess was stopped there, since it was a guess.

So the run cannot tell whether the report was filed and then closed before 09:10 PT, or has not been filed. Both leave the card out of reach of the readers the run holds. Neither is written as a number.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** Used for every GitHub read above: the pull-request searches, the `stable` ref, the deployments of the `pipeline-channel` environment and their statuses, the console tags and the compares, and `scripts/proof_release.py`. The step summary's `proof identity: github read — …` line was not available to this run, so its permissions are not copied here.
  - The one refused write: `GH_TOKEN=$GH_READ_TOKEN gh api --method POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-41329576913 -f sha=117b5a0ee32d6684e7c62e40834287ad7ad93ac1`.
  - GitHub answered `{"message":"Resource not accessible by integration",…,"status":"403"}` (`gh: Resource not accessible by integration (HTTP 403)`). No ref was created.
- **GitHub, written: `GH_TOKEN`.** Not used by this run. A shadow run pushes nothing, and the workflow commits this file itself.
- **Linear: `LINEAR_API_KEY`, the fleet key.** Used only for reads, with `BUREAU_LINEAR_READ_ONLY=1` set: `find-open` twice, `find-open-prefix`, `state-of DRE-7512`, `description DRE-7512` and `description` on ten neighbouring identifiers. No card was moved, edited or commented on.
  - linear requests: 15 of 40, summed from the `linear-calls:` lines (each of the 15 invocations printed `1 request(s)`).
- **AWS:** aws: none — no role was provided, and no criterion on this card needs one.
- **The console proof-reader:** not used. The card has no `Sandbox press:` criterion, and this checkout holds no `scripts/console_proof_reader.py`.
- No scratch state was created and no hosted sign-in was made.

## Criterion table

| Criterion | Result |
| -- | -- |
| The record names, in PT, when bureau-pipeline's `stable` first carried the nine bureau-pipeline cards above and when the agent-bureau release carrying DRE-7809, DRE-7796 and DRE-7797 went live, both read off the release record. It then names the proof week (the first Sunday-to-Saturday PT window starting at or after the later of the two) and the not-before time its report can first be read, and names any straddling report it set aside, with why. | Met. **`stable` first carried all nine:** 2026-10-24 05:20:27 PT, the `success` status of the `pipeline-channel` release deployment 7298508520 at `f3a9c80` (DRE-7802's merge, the last of the nine). `proof_release.py check` reads `ready — … stable carries` for #1236 and #1213. **The console release went live** as `agent-bureau-console-v1.6.587`, tagged 2026-10-23 16:32:24 PT, the first tag that answers `behind` for all three merges (`e1907ac`, `aa34a25`, `970ae6e`). **The proof week** is Sunday 2026-10-25 to Saturday 2026-10-31 PT, and its report can first be read at `2026-11-01T09:00-08:00` (09:00 PT, the Sunday after, in Pacific standard time). **The straddling report set aside:** the week ending 2026-10-24, because its window began before both releases and all of it but the last 18 hours 40 minutes ran on the old medic and build path. |
| The record quotes the proof week's diagnosed-then-idle hours from the report's "Frozen weeks" section and states them against the baseline 1,688.5 hours (week ending 2026-10-17), as a difference in hours and as a share. Until the report exists, it is a stamped waiting row in the form above. | Not observed. cause: out-of-reach — at 09:10 PT, after the 09:00 PT not-before time, `find-open "What broke the agents — week ending 2026-10-31 (PT)"` printed nothing, and so did `find-open-prefix "What broke the agents"`. Both skip closed cards, and no reader the proof identity holds returns a card by title in a closed state, so the report card's "Frozen weeks" line `2026-11-01: diagnosed-then-idle …` could not be read. The baseline stays 1,688.5 hours (re-read off DRE-7512 at 09:10 PT). |
| The record quotes the report card's `medic/run-failure-diagnosed` breakdown: by conclusion, moved next by, and what the medic did (re-ran, sent to Planning, named the fix loop, asked a person, no step), the five counts DRE-7797 adds to that breakdown. It also quotes the stops table's next-actor cell for that row. It states how many diagnosed failures the medic moved itself against the baseline, where all 201 were read as `itself` and 21 had not moved at all, and how many it parked in Green Light against the expected 18 to about 79. Until the report exists, it is a stamped waiting row. | Not observed. cause: out-of-reach — the report card for the week ending 2026-10-31 could not be reached (see the row above), so the `medic/run-failure-diagnosed` line, DRE-7797's five counts and the stops table's next-actor cell for that row were not read. The baseline's line, re-read at 09:10 PT, is `medic/run-failure-diagnosed: 201 — by conclusion: bad-code 178, platform-fault 18, transient-infrastructure 3, bad-card 2; moved next by: a sweep 31, a person 6, a merge 143, nothing yet 21.` with next actor `itself`. |
| The record quotes, off the same report card, what moved the `fix-path` cards next, as DRE-7797 prints it after `of the fix-loop cases, moved next by`. It states the share that nothing moved against the baseline's 21 of 201 (about 10%), which is the reading of whether the fix loop picked up the cards the medic named to it. Until the report exists, it is a stamped waiting row. | Not observed. cause: out-of-reach — the same report card could not be reached, so the text after `of the fix-loop cases, moved next by` was not read and no share was computed. The baseline to set it against is 21 of 201, 10.4%. |
| The record reads, off the same report card's description, the week's count of vendor-error deaths that spent a strike and the count that waited, as DRE-7797 prints them, and each `run-failed/api_error/…` breakdown. It states both counts against the baseline of 28 deaths with none read as a wait, and against the expectation above (renewal-window 401s wait, the others stay strikes). It also says how many of the week's deaths carried a 529 or 5xx. Until the report exists, it is a stamped waiting row. | Not observed. cause: out-of-reach — the same report card could not be reached, so the struck and waited counts, each `run-failed/api_error/…` line and the count of 529 or 5xx deaths were not read. The baseline to set them against, re-read at 09:10 PT, is 28 deaths, all 401s, 13 inside a credential renewal window and 15 outside, none read as a wait. |
| For one card the report's `medic/run-failure-diagnosed` samples name, the record reads that card's comments with `linear_ops.py dump-comments` and its lane with `state-of`, as the proof-reader identity. It quotes the card's `run-failure-diagnosed` comment and the comment posted after it under the `medic-next-step` act (the receipt DRE-7792 renders, naming class, step and run), with both PT times and the card's lane after the receipt. When that receipt names the `ask-person` step, the record also quotes the three-line question posted before the move to Green Light, as `console_escalation.py` rendered it. Until the report exists, it is a stamped waiting row. | Not observed. cause: out-of-reach — the card is named by the report's samples, and the report card could not be reached, so no card was chosen and none was read with `dump-comments` or `state-of`. No card was picked by any other route, since a card not named by the report's samples would not be the card the criterion asks for. |
| The record's criterion table judges every row above as `Met.`, `Not met.` or `Not observed.` (a waiting row in the stamped form while the report does not exist yet), and a closing "What is not proven, and why" section says what one week cannot show. | Met. Every row above opens with `Met.` or `Not observed.`, and the record closes with "What is not proven, and why", below. The five `Not observed.` rows carry the cause `out-of-reach`, not a waiting stamp, because the run could not establish that the report does not exist yet: it could only establish that no open card carries its title. |

## What is not proven, and why

- **Nothing about the new medic is observed.** This run read the release record and the baseline only. Every reading of what the medic and the build path did waits on the report for the week ending 2026-10-31, which this run could not reach. Whether that report is simply closed already or has not been filed is not known here.
- **A closed report card is out of reach of the allowed readers.** `find-open` skips completed and canceled cards, and the report is meant to be read and closed. A re-run that is handed the report's identifier, or a reader that finds a card by title in any state, reads the same rows in one pass.
- **One week is one mix of failures.** The plan's expectations — up to 3 re-runs, 2 sent to Planning and 18 to about 79 questions — are scaled from the baseline week's mix: 178 `bad-code`, 18 `platform-fault`, 3 `transient-infrastructure` and 2 `bad-card`. A week with a different mix can land outside those numbers with the mechanism working, or inside them with it broken. The three earlier frozen weeks (1,253.6, 1,512.1 and 1,688.5 hours) moved by hundreds of hours with no change to the medic.
- **Diagnosed-then-idle hours depend on more than the medic.** The hours fall when the fix loop, the sweep and people move the cards, not only when the medic acts. A drop in hours does not by itself show which of them moved a card, which is why the criteria read the moved-next-by and what-the-medic-did counts beside the hours.
- **Vendor-error waits depend on the vendor.** A week with no credential renewal window, or no 401 at all, has nothing to wait on. In that week a struck count and a waited count of zero would show nothing about DRE-7795's reader. The baseline's 13 renewal-window 401s are not a promise of 13 in the proof week.
- **A 401 outside a renewal window stays a strike by design.** DRE-7795 reads the pipeline's own renewal 401 by its wording, so a strike on an unknown 401 is the expected result, and this proof cannot show that an unknown 401 should have waited.
- **One sampled card shows one path.** It shows one class, one step and one receipt, and cannot show that every class's step fires. The other classes are read only as counts on the report.
- **The report is the only witness.** Every number the criteria ask for is the console's own weekly report about the pipeline. This proof checks what the report says, not the runs underneath it. A miscount in DRE-7797's breakdown would be carried here as written.
