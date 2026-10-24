# PROOF record — DRE-7810: one full week's stop report, read after the change ships, shows the medic moving what it diagnoses and vendor refusals waiting instead of striking — recorded in the repo (epic DRE-6681)

**Status: PARTIAL.**

The proof for [DRE-7810](https://linear.app/dreadnoughtfoundry/issue/DRE-7810).
Epic [DRE-6681](https://linear.app/dreadnoughtfoundry/issue/DRE-6681) has the
medic act on its own diagnosis and treat a vendor's refusal as a wait. This
record reads whether the first full week after that change shows it, off the
weekly stop report.

**What this record holds on the day it was written.** The release half is
observed: both releases are live, and the proof week is fixed. The report half
is not observable yet. The week it reads begins on Sunday 2026-10-25 and ends on
Saturday 2026-10-31 (PT), and its report is filed the Sunday after. Every row
that reads the report is therefore a stamped waiting row, as the card asks, and
the sweep re-runs the record on its own once the stamp passes.

## How this was recorded

- **Run:** [40094115711](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/40094115711),
  attempt 1, on `dreadnought-foundry/bureau-pipeline`. It is the shadow run of
  DRE-7286 on `claude-sonnet-5-5`: it observes every row as the real run would,
  makes no sandbox press, and writes only this file.
- **Observed between** 2026-10-24 05:30 PT and 05:50 PT (Saturday).
- **Commit observed:** bureau-pipeline `main` and `stable` both at `f3a9c80`
  (merge of #1236, DRE-7802). agent-bureau console tag
  `agent-bureau-console-v1.6.587`, commit `636acb4`.
- **Reads made:** GitHub as `GH_READ_TOKEN` (pull requests, tags, compare,
  Promote Channel run logs); Linear as `linear_ops.py`
  (`description`, `children-detail`, `epics-in-flight`).
- `proof_release.py check` was run for the nine bureau-pipeline cards and read
  `ready` for each. The same command against agent-bureau did not finish inside
  100 seconds: it reads the date of every tag in the console series, 625 of
  them. The console half below was read by hand with the rule that script
  states (the newest tags, each one's commit, then `compare` for each merge),
  and is marked as such.

### The baseline, read again

The baseline is the report for the week ending 2026-10-17, DRE-7512, read with
`python3 .bureau-pipeline/scripts/linear_ops.py description DRE-7512` at
2026-10-24 05:35 PT. It reads as the card says:

- Frozen weeks: `2026-10-18: diagnosed-then-idle 1,688.5 hours, vendor-error deaths 28`
- Stops table: `medic | run-failure-diagnosed | 201 | 0 | $0.00 | 1,688.5 | 0.0 | 0 | itself | falling`
- `medic/run-failure-diagnosed: 201 — by conclusion: bad-code 178, platform-fault 18, transient-infrastructure 3, bad-card 2; moved next by: a sweep 31, a person 6, a merge 143, nothing yet 21.`
- The four `run-failed/api_error/…` lines count 11, 8, 7 and 2 deaths, every
  one a 401 (28 in all). They read 8, 1, 3 and 1 inside a credential renewal
  window, 13 in all and 15 outside. None is read as a wait.

That report has no "what the medic did" counts and no waiting count. DRE-7797
adds them, so the proof week's report is the first that can carry them.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** Every GitHub read above. The step
  summary's `proof identity: github read — …` line was not handed to this run,
  so the permission list is not copied here.
- **GitHub, written: `GH_TOKEN`.** Not used. A shadow run pushes nothing.
- **The refused write.** Made with the read token:
  `GH_TOKEN=$GH_READ_TOKEN gh api --method POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-40094115711 -f sha=f3a9c801770609db01875735e941e570550243e2`.
  GitHub answered `{"message":"Resource not accessible by integration", … "status":"403"}`.
  No ref was created, so there is nothing to clean up.
- **Linear: the fleet key**, read-only here (`BUREAU_LINEAR_READ_ONLY=1`). It
  was used for reads only. The shadow run posts no heartbeat, marker or
  escalation. `linear requests: 5 of 40`.
- **AWS:** `aws: none` — no role was handed to this run.
- **The console proof-reader:** not used. This record has no `Sandbox press:`
  row, and a shadow run makes none.
- **Local run:** `PROOF_LOCAL_STATUS=none`. No criterion here is a `Local screen:`
  row.
- No scratch state was created and no hosted sign-in was made.

## The release record, in detail

**bureau-pipeline, `stable`.** `stable` is a moving channel tag
(`.github/bureau/release.json`, `record: channel`). It moved to `f3a9c80` in
Promote Channel run
[40093292063](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/40093292063),
whose "Move the channel" step answered at 2026-10-24 12:20:27 UTC
(**05:20:27 PT**) and whose "Record the release" step wrote
`release recorded: deployment 7298508520 (stable@f3a9c80)`. `f3a9c80` is the
merge of #1236 (DRE-7802), the last of the nine, merged 2026-10-24 04:10:58 PT.
The two runs before it did not carry it: the 04:19 PT run (40089472149) held
with `unit-suite-not-finished`, and the 04:49 PT run (40091455271) compared
candidate `f9c2a77` and read `ANCESTRY: identical`, so `stable` was still at
`f9c2a77`, which has DRE-7805 but not DRE-7802.

| Card | Pull request | Merged (PT) | `proof_release.py` |
| -- | -- | -- | -- |
| DRE-7792 | #1213 `ad63912` | 2026-10-23 15:49 | ready — stable carries |
| DRE-7793 | #1219 `459e6db` | 2026-10-23 17:18 | ready — stable carries |
| DRE-7799 | #1229 `141a37f` | 2026-10-23 21:03 | ready — stable carries |
| DRE-7794 | #1232 `c462dae` | 2026-10-23 22:57 | ready — stable carries |
| DRE-7805 | #1234 `f9c2a77` | 2026-10-24 01:44 | ready — stable carries |
| DRE-7795 | #1212 `d389318` | 2026-10-23 16:07 | ready — stable carries |
| DRE-7800 | #1221 `e796604` | 2026-10-23 17:45 | ready — stable carries |
| DRE-7801 | #1227 `1a84cd0` | 2026-10-23 20:08 | ready — stable carries |
| DRE-7802 | #1236 `f3a9c80` | 2026-10-24 04:10 | ready — stable carries |

**agent-bureau, console.** The console's record is its annotated tag
(`record: tag`, series `agent-bureau-console-v*`). The newest tags were read
with `git/matching-refs` and `git/tags/<sha>`, and each merge was tested with
`compare/<merge>...<tag commit>`:

| Tag | Created (PT) | Commit | DRE-7809 (#4111) | DRE-7796 (#4112) | DRE-7797 (#4115) |
| -- | -- | -- | -- | -- | -- |
| `agent-bureau-console-v1.6.585` | 2026-10-23 15:17 | `e1907ac` | carried | not carried | not carried |
| `agent-bureau-console-v1.6.586` | 2026-10-23 15:54 | `04b6ba4` | carried | carried | not carried (`behind`) |
| `agent-bureau-console-v1.6.587` | 2026-10-23 16:32 | `636acb4` | carried | carried | carried (`ahead`) |

The pull requests merged 2026-10-23 14:55 PT (#4111), 14:59 PT (#4112) and
15:27 PT (#4115). The GitHub deployments list answered `403`
(`Resource not accessible by integration`) for the read token, so the tag's
creation time is the release record this run could read.

## Criterion table

| Criterion | Result |
| -- | -- |
| The record names, in PT, when bureau-pipeline's `stable` first carried the nine bureau-pipeline cards above and when the agent-bureau release carrying DRE-7809, DRE-7796 and DRE-7797 went live, both read off the release record. It then names the proof week (the first Sunday-to-Saturday PT window starting at or after the later of the two) and the not-before time its report can first be read, and names any straddling report it set aside, with why. | Met. `stable` first carried all nine at **2026-10-24 05:20 PT** (Promote Channel run 40093292063, deployment 7298508520, `stable@f3a9c80`; `proof_release.py check` reads `ready` for all nine). The agent-bureau console release carrying DRE-7809, DRE-7796 and DRE-7797 went live with tag `agent-bureau-console-v1.6.587`, created **2026-10-23 16:32 PT**, read by hand because `proof_release.py` did not finish on agent-bureau's 625 console tags. The later of the two is 2026-10-24 05:20 PT, so the proof week is **Sunday 2026-10-25 to Saturday 2026-10-31 (PT)**, the week ending 2026-10-31. Its report is filed the Sunday after, and can first be read at **2026-11-01 09:00 PST** (`2026-11-01T09:00-08:00`; daylight time ends at 02:00 that day). One straddling report is named: the one for the week ending 2026-10-24 (Sunday 2026-10-18 to Saturday 2026-10-24), due Sunday 2026-10-25. Its window began a week before the later release, which landed in its last morning, so six days of it ran on the old medic. It is set aside for that reason. It does not exist yet, so nothing has been set aside so far, and the sweep's re-run will set it aside if it is filed before the proof week's report. |
| The record quotes the proof week's diagnosed-then-idle hours from the report's "Frozen weeks" section and states them against the baseline 1,688.5 hours (week ending 2026-10-17), as a difference in hours and as a share. Until the report exists, it is a stamped waiting row in the form above. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: its "Frozen weeks" line for the week (`<date>: diagnosed-then-idle <hours> hours`), stated against the baseline 1,688.5 hours as a difference in hours and as a share |
| The record quotes the report card's `medic/run-failure-diagnosed` breakdown: by conclusion, moved next by, and what the medic did (re-ran, sent to Planning, named the fix loop, asked a person, no step), the five counts DRE-7797 adds to that breakdown. It also quotes the stops table's next-actor cell for that row. It states how many diagnosed failures the medic moved itself against the baseline, where all 201 were read as `itself` and 21 had not moved at all, and how many it parked in Green Light against the expected 18 to about 79. Until the report exists, it is a stamped waiting row. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: the `medic/run-failure-diagnosed` line with its by-conclusion, moved-next-by and what-the-medic-did counts, the stops table's next-actor cell for that row, the count the medic moved itself against the baseline 201 of 201 read as `itself`, and the count parked in Green Light against the expected 18 to about 79 |
| The record quotes, off the same report card, what moved the `fix-path` cards next, as DRE-7797 prints it after `of the fix-loop cases, moved next by`. It states the share that nothing moved against the baseline's 21 of 201 (about 10%), which is the reading of whether the fix loop picked up the cards the medic named to it. Until the report exists, it is a stamped waiting row. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: the text after `of the fix-loop cases, moved next by` and the share nothing moved, against the baseline's 21 of 201 (about 10%) |
| The record reads, off the same report card's description, the week's count of vendor-error deaths that spent a strike and the count that waited, as DRE-7797 prints them, and each `run-failed/api_error/…` breakdown. It states both counts against the baseline of 28 deaths with none read as a wait, and against the expectation above (renewal-window 401s wait, the others stay strikes). It also says how many of the week's deaths carried a 529 or 5xx. Until the report exists, it is a stamped waiting row. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: the counts of vendor-error deaths that spent a strike and that waited, each `run-failed/api_error/…` breakdown, both against the baseline's 28 deaths (13 inside a renewal window, 15 outside, none read as a wait) and the expectation that renewal-window 401s wait while the others stay strikes, and how many deaths carried a 529 or 5xx |
| For one card the report's `medic/run-failure-diagnosed` samples name, the record reads that card's comments with `linear_ops.py dump-comments` and its lane with `state-of`, as the proof-reader identity. It quotes the card's `run-failure-diagnosed` comment and the comment posted after it under the `medic-next-step` act (the receipt DRE-7792 renders, naming class, step and run), with both PT times and the card's lane after the receipt. When that receipt names the `ask-person` step, the record also quotes the three-line question posted before the move to Green Light, as `console_escalation.py` rendered it. Until the report exists, it is a stamped waiting row. | Not observed. waiting for the weekly report for the week ending 2026-10-31 [not-before=2026-11-01T09:00-08:00]: one card named in its `medic/run-failure-diagnosed` samples, with that card's `run-failure-diagnosed` comment, the `medic-next-step` receipt after it (class, step, run), both PT times, the lane after the receipt, and the three-line question if the step is `ask-person` |
| The record's criterion table judges every row above as `Met.`, `Not met.` or `Not observed.` (a waiting row in the stamped form while the report does not exist yet), and a closing "What is not proven, and why" section says what one week cannot show. | Met. Every row above opens with `Met.` or `Not observed.`, and the five rows that read the report are waiting rows in the stamped form with the same `[not-before=2026-11-01T09:00-08:00]`. The closing section below says what one week cannot show. |

## What is not proven, and why

- **Nothing about the medic's behavior is proven here.** The release half is
  observed: the changes are live and the week to read is fixed. Whether the
  medic moved what it diagnosed, and whether vendor refusals waited, is read
  off a report that does not exist until 2026-11-01. This record observed none
  of it, and it should not be taken to say the change works.
- **One week is one mix of failures.** The plan's numbers are set at last
  week's mix: 201 diagnoses, 178 `bad-code`, 3 transient and 2 `bad-card`. A
  quieter or noisier week moves every count without the medic changing. For
  example, the medic can only re-run what it reads as transient, and at last
  week's mix that is 3. A week with a different mix cannot be judged against
  those figures.
- **The vendor half depends on what the vendor does.** The baseline held 28
  deaths, all 401, and no 529 or 5xx. A week with few vendor deaths gives the
  wait reader little to act on, and a week with none inside a renewal window
  gives it nothing it is expected to wait on. The 15 deaths outside a window
  are expected to stay strikes, so 13 waits is the most the baseline mix can
  show.
- **Whether the fix loop picked the cards up is a slower question.** The report
  prints what moved a `fix-path` card next by the end of its own week. A card
  named to the fix loop late in the week can read `nothing yet` and still be
  picked up on Monday. The 21 of 201 baseline is read by the same end-of-week
  rule, so the comparison is fair, but a single week cannot separate a slow
  pickup from none.
- **The week straddling the release is set aside, not measured.** The report
  for the week ending 2026-10-24 ran six days on the old medic. Reading it
  would blur the two, and so it is not used.
- **The console's go-live time is a tag, not a deployment.** The GitHub
  deployments list was not readable by the read token, so the console release
  time is the tag's creation time. The Linear Releases entry that could
  confirm the deploy itself was not read.
- **A report can be wrong in ways this record cannot see.** DRE-6913 corrected
  an earlier report after it was filed. This proof reads the report as filed
  and will name a later correction if one appears before the re-run.
