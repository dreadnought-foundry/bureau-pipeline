# PROOF record — DRE-5862: PROOF: Linear requests per sweep pass and per phase, before and after the remaining reads moved to our database (epic DRE-5852)

**Status: PARTIAL.**

**The headline.** In bureau-pipeline's own sweep, two of the three moves
worked and one did not. The Intake, Planning and Green Light reads
(DRE-5848, with DRE-5859 and DRE-5860) left Linear: about 2.2 requests a pass,
about 9 an hour. The long epic threads (DRE-5850, with DRE-5849 and
DRE-5861) are served by the door whenever the door is fresh: 4 requests a pass
on a quiet pass, about 16 an hour at full service. The unlanded-work
watchdog's card-lane read (DRE-5847) **never** came from the door: in 153 of
the 154 full passes since it merged, the line reads `0 from the door in one
read, 3 read from Linear (the door could not answer them)`, and the other one
reads `0 from the door in one read, 0 read from Linear`. The door's reason is
`missing-field` (126 runs) or `stale` (28 runs), and the four cards it is
asked about are all Done. **The watchdog's read keeps coming back unknown
because the door does not hold finished cards**, 154 passes out of 154, and
per the card this is a mid-epic addition, not a footnote.

What I could not observe: agent-bureau's and Portico's sweeps, and the fleet
key's hourly spend. The read-only identity this run holds can read exactly one
repository, bureau-pipeline, so every number below is bureau-pipeline's, and
the agent-bureau and Portico halves are `Not observed.` for that reason.

## How this was recorded

- **Run:** Proof Task run 37645497359,
  <https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37645497359>,
  started 2026-10-07 08:38 PT, on bureau-pipeline `main` at `b2c61a25`.
- **Release observed:** bureau-pipeline's own sweep rides `@main` (the canary
  channel, `.github/workflows/self-reconcile.yml` calls `reconcile.yml@main`
  with `pipeline_ref: main`), so a merge is live at the next pass. DRE-5847
  merged 2026-10-05 09:11 PT (PR #732), DRE-5848 09:27 PT (PR #731), DRE-5850
  2026-10-06 09:47 PT (PR #760). DRE-5859 closed 2026-10-05 10:13 PT
  (agent-bureau PR #3179), DRE-5860 at 11:36 PT (the CEO's deploy, 11:13 PT),
  DRE-5849 2026-10-06 08:51 PT (agent-bureau PR #3220), DRE-5861 2026-10-07
  08:36 PT (its dry run selected 0 cards, so no live backfill ran). Completion
  times are Linear's `completedAt`, read with the fleet key.
- **What was read:** the logs of every Reconcile run in bureau-pipeline from
  the first one listed on 2026-10-04 (08:02 PT) to 2026-10-07 08:36 PT — 300 runs listed, 296 logs fetched
  (4 were empty or cancelled), 223 of them full passes — fetched as
  `GH_TOKEN=$GH_READ_TOKEN gh api repos/dreadnought-foundry/bureau-pipeline/actions/runs/<id>/logs`
  between 08:41 and 08:46 PT. Each pass's `sweep-spend:` lines, its
  `unlanded: card lanes —` and `promotion: epic threads —` lines, and its
  `read-door:` lines are quoted as the pass printed them.
- **The unit** is the sweep's own `sweep-spend: <phase> <n> request(s)` line.
  A phase that spent nothing prints no line; it is shown as 0 below.
- **Labels.** `measured` is a number a log printed. `estimated` is arithmetic
  on measured numbers (an average, or a per-pass figure times passes an hour)
  and says so. All times are Pacific (PDT).
- **Passes an hour.** bureau-pipeline ran 13 full passes on the busy afternoon
  of 2026-10-04 13:30–16:50 PT (3.9 an hour, measured) and 14 on 2026-10-06
  over the same window (4.2 an hour, measured). Every per-hour figure below
  uses 3.9.

## Identities

- **GitHub, observed — `GH_READ_TOKEN`.** Every GitHub read above. Its
  installation reaches exactly one repository:
  `GH_TOKEN=$GH_READ_TOKEN gh api installation/repositories` answered
  `total_count: 1`, `dreadnought-foundry/bureau-pipeline`. Asking it for
  agent-bureau's or Portico's workflows answered `404 Not Found`.
  **The refused write**, 2026-10-07 08:47 PT:
  `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-37645497359 -f sha=b2c61a250be9407ef7ebda8825db79dbe26148fe`
  — GitHub answered `403`, `Resource not accessible by integration`. No ref
  was created.
- **GitHub, written — `GH_TOKEN`.** Used only to push
  `agent/DRE-5862-proof-record` and open its pull request. It observed
  nothing.
- **Linear — `LINEAR_API_KEY` (the fleet key).** The heartbeats on DRE-5862,
  the closing actor marker, and reads: the state and `completedAt` of the
  seven blocking cards, their comments, and the lanes of the four cards the
  watchdog asks about. It wrote nothing else and touched no other card.
- **aws: none.** No proof role was provided, and no criterion here needs one.
- **Scratch state:** none created.

## Criteria

| Criterion | Result |
| --- | --- |
| The record is merged at `architecture/audits/read-door-remaining-reads-proof-2026-10.md` and holds the `sweep-spend:` lines per phase and per pass, before and after, for five full sweeps each in agent-bureau and bureau-pipeline plus Portico as the control, observed on the live fleet from the real run logs, with run links, times in PT, and every number labeled measured or estimated. | Not observed. agent-bureau and Portico: the read-only token reaches only bureau-pipeline (`installation/repositories` total 1; agent-bureau and Portico workflow listings answered 404), so neither repo's sweep logs could be read without the worker token, which may not observe. bureau-pipeline: observed in full — the per-phase lines of the last five full passes before DRE-5847/DRE-5848 merged (2026-10-05 07:53–09:06 PT), the last five before DRE-5850 merged (2026-10-06 08:25–09:30 PT), and five after (2026-10-07 07:28–08:36 PT), each with its run link, under "bureau-pipeline, pass by pass" below. Every number is labeled. The record merges with this pull request. |
| The record states, per build card (DRE-5847, DRE-5848, DRE-5850), the requests per pass its phase spent before and after and the requests an hour that removes, and names any phase whose movement belongs to other work. | Not observed. for the fleet; stated for bureau-pipeline's sweep (measured per pass, estimated per hour at 3.9 passes an hour). DRE-5847 `flag_unlanded_work`: 6.2 → 3.8 a pass, but its door read served 0 cards in every after pass, so it removes 0 an hour; the phase's drop is not its doing. DRE-5848 `recover_limit_deaths` + `serve_planner_line`: 2.6 → 0.4 a pass, about 8.6 an hour removed. DRE-5850 `promote_ready` epic threads: 5 → 1 on a quiet pass when the door serves (4 a pass, about 15.6 an hour at full service; about 9.4 an hour at the 3-in-5 service the after passes saw). Other work named: `report_intake_blockers` (DRE-4152, +3 a pass since 2026-10-06 15:35 PT), `release_groom_queue` (the groomer's batch, 2 to 36), `settle_repair_cards` and `fix_approved_but_red`. agent-bureau's half, where the card's 10-04 baseline was measured, could not be read (row 1). |
| The record quotes the live door/Linear split lines from DRE-5847 and DRE-5850 for the after sweeps, with the fallback reason wherever a read went to Linear. | Not observed. for agent-bureau and Portico (row 1). bureau-pipeline's five after passes are quoted verbatim below with every reason. DRE-5847: `unlanded: card lanes — 0 from the door in one read, 3 read from Linear (the door could not answer them)` in 5 of 5, behind `read-door: unlanded card lanes unknown (missing-field: …)` in 3 and `(stale: …)` in 2. DRE-5850: `promotion: epic threads — 2 from the door in one read, 0 read from Linear (none)` in 3 of 5 and `0 from the door in one read, 2 read from Linear (stale)` in 2. |
| The record states the fleet key's requests in one busy afternoon hour after, against the 650 before, measured the same way, and the `linear-calls:` trailer of one planner run and one build run before and after. | Not observed. The fleet key's hour: `GH_TOKEN=$GH_READ_TOKEN python3 scripts/check_linear_budget.py --hours 1` at 08:42 PT printed `UNKNOWN` for agent-bureau, agent-bureau-demo, atlas, deltasolv and portico (`HTTP 404`), so no fleet total exists; bureau-pipeline alone was 387 (267 on the fleet key, 120 on the planner's own bucket) for 07:42–08:42 PT, a morning hour, not the afternoon the card asks for. The trailers were observed (measured, not changed by this epic): planner run 39 requests before (DRE-5807, 2026-10-04 14:08 PT) and 38 after (DRE-6018, 2026-10-07 07:22 PT), both on `budget: planner-oauth`; build run 14 before (DRE-5801, 2026-10-04 14:08 PT) and 14 after (DRE-6018, 2026-10-07 07:29 PT), both on `budget: fleet`. Links below. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## DRE-5847 — the watchdog's card lanes never came from the door

The card's own words for this case: if the watchdog's read keeps coming back
unknown because the door does not hold finished cards, the record says so in
those words, with the count, and it becomes a mid-epic addition.

**It keeps coming back unknown.** Every full pass since DRE-5847 merged
(2026-10-05 09:11 PT) through 2026-10-07 08:36 PT — 154 of them — printed
`0 from the door in one read`: 153 with `3 read from Linear` and one with
`0 read from Linear` (measured). The door's reasons, counted over every
Reconcile log in the window (full and scoped passes both):

- `read-door: unlanded card lanes unknown (missing-field: the door answered UNKNOWN)` — 126 (measured)
- `read-door: unlanded card lanes unknown (stale: the door answered UNKNOWN)` — 28 (measured)

**Why, as far as the logs and the board show.** The read asks the door's
`/cards` for the card behind every `agent/DRE-*` branch that has no pull
request among the last 100, and `/cards` is all-or-nothing. On 2026-10-07 at
08:45 PT (the branch and pull-request listings, read-only) those cards were DRE-3639, DRE-4103, DRE-4124 (two branches) and
DRE-4973, and every one is Done in Linear (read with the fleet key). The
door's own word is `missing-field`, not "does not hold this card", so the
cause is inferred from the four cards' lanes, not printed by the door. A
door that held finished cards would answer them, and each pass would save its
3 Linear reads (3 a pass, about 11.7 an hour at 3.9 passes, estimated).

**This is a mid-epic addition.** I file nothing (a proof run writes no card);
the addition is the operator's or the planner's to file against DRE-5852 with
`scripts/mid_epic.py discovery DRE-5852 --kind addition`.

## Per build card — bureau-pipeline's sweep

All per-pass numbers are averages of the five passes named in the next
section (estimated, from measured lines). Per hour is per pass × 3.9
(estimated).

| Card | Phase(s) | Before, a pass | After, a pass | Removed, a pass | Removed, an hour |
| --- | --- | --- | --- | --- | --- |
| DRE-5847 | `flag_unlanded_work` (card-lane read) | 6.2 (6, 6, 8, 6, 5) | 3.8 (3, 5, 3, 3, 5) | 0 by this card: the door served 0 of 4 cards in 5 of 5 after passes | 0 |
| DRE-5848 (+ DRE-5859, DRE-5860) | `recover_limit_deaths` + `serve_planner_line` | 2.6 (3, 3, 1, 3, 3) | 0.4 (0, 1, 0, 0, 1) | 2.2 | about 8.6 |
| DRE-5850 (+ DRE-5849, DRE-5861) | `promote_ready`, epic-thread reads | 5 on 26 of the 28 passes 2026-10-06 00:05–08:05 PT (the other two promoted cards and spent 9) | 1 on 24 of the 24 passes 2026-10-06 23:41 – 2026-10-07 07:04 PT | 4 when the door serves, 0 when it falls back | about 15.6 at full service; about 9.4 at the 3-in-5 service the after passes saw |

**Reading the table.**

- **DRE-5847.** The phase fell from 6.2 to 3.8, but not because of this card:
  its card-lane read still costs 3 Linear reads a pass, exactly as before. The
  phase reads 5, not 3, on a pass where the door's work-lane board read also
  falls back (`read-door: board unknown (stale: …) — read from Linear this
  pass` sits inside the phase), and it read 8 a pass on 2026-10-04 with more
  PR-less branches on the board. Neither is this card's.
- **DRE-5848.** Before the merge, `recover_limit_deaths` paid 2 for the
  Intake/Planning read and `serve_planner_line` paid 1 for Green Light, every
  pass. Between the merge and the CEO's deploy the pass at 11:06 PT
  ([37353489373](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37353489373))
  printed `read-door: Intake/Planning read from Linear this pass — the door does not serve scope=fleet to this repo (lane-not-held)`;
  from 11:33 PT
  ([37356813607](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37356813607))
  no such line, and on every pass since where the door answered fresh, neither
  phase spent anything but one `serve_planner_line 2` at 2026-10-05 13:05 PT
  (measured over 145 passes). On a
  stale-door pass Green Light falls back: `read-door: Green Light read from Linear this pass — the door does not serve scope=fleet to this repo (stale)`,
  and `serve_planner_line` spends 1 (2 of the 5 after passes).
- **DRE-5850.** The five passes before the merge spent 2, 6, 9, 13, 13 in
  `promote_ready` and the five after 17, 6, 1, 1, 13 (measured), but that
  phase also pays for promotions, which come and go. The quiet passes isolate
  the thread reads: 5 on 26 of 28 passes the night before the merge, 1 on
  24 of 24 the night after the door first served the threads. Between the
  merge and 2026-10-06 18:09 PT the epic threads fell back as
  `(thread-incomplete: DRE-3624, DRE-5129)` and then `(thread-incomplete: DRE-5129)`,
  until the door held those threads whole; since then the only fallback reason
  printed is `stale`.

**Other phases, other work** (named so they are not credited to this epic):

- `report_intake_blockers` — DRE-4152, merged 2026-10-06 15:35 PT; 3 a pass
  on every pass since (measured). It reads Intake through Linear on its own.
- `release_groom_queue` — the groomer releasing an approved batch: 2 on a
  quiet pass, 12 at 08:05 PT and 36 at 08:36 PT on 2026-10-07 (measured).
- `settle_repair_cards` (4) and `fix_approved_but_red` (1) at 08:36 PT on
  2026-10-07 — the red-main repair rail and the fix loop.
- `refresh_stale_merge_refs`, `sweep_idle`, `flag_stranded`,
  `close_finished_epics`, `tend_epic_queue`, `start_queued_epics`,
  `report_break_glass`, `report_rereview_missing` — untouched by this epic.

**The board, as the passes printed it.** In Progress epics named by the
pass's `epic-growth:` report: 11, 11, 11, 11, 10 before DRE-5847/DRE-5848;
7 ×5 before DRE-5850; 5 ×5 after (measured). Epics past 50 comments: 2 in
every after pass (the `promotion: epic threads —` count); the line did not
exist before DRE-5850, so the before count is not printed (not observed).
PR-less stale branches' cards asked of the door: 4, 4, 4, 4, 5 before
DRE-5850 and 4 ×5 after (measured); the line did not exist before DRE-5847.
The after board is smaller than the before boards, which flatters the
`after` totals — the per-phase lines are what to read.

## bureau-pipeline, pass by pass

Every line below is the pass's own `sweep-spend:` line, phase and count, in
the order printed (measured). Run links go to the GitHub Actions run.

### Before DRE-5847 and DRE-5848 — the last five full passes before 2026-10-05 09:11 PT

| Pass | Run | `sweep-spend:` lines |
| --- | --- | --- |
| 2026-10-05 07:53 PT | [37328360431](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37328360431) | flag_unlanded_work 6 · recover_limit_deaths 2 · flag_stranded 1 · serve_planner_line 1 · release_groom_queue 2 · close_finished_epics 4 · promote_ready 5 · report_break_glass 1 · report_rereview_missing 4 · total 26 over 9 phase(s) |
| 2026-10-05 08:05 PT | [37329997285](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37329997285) | flag_unlanded_work 6 · recover_limit_deaths 2 · report_fleet_reviewer_outage 1 · flag_stranded 1 · serve_planner_line 1 · release_groom_queue 2 · close_finished_epics 4 · promote_ready 2 · report_break_glass 1 · report_rereview_missing 6 · total 26 over 10 phase(s) |
| 2026-10-05 08:28 PT | [37333149753](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37333149753) | sweep_idle 1 · refresh_stale_merge_refs 2 · flag_unlanded_work 8 · report_fleet_reviewer_outage 1 · serve_planner_line 1 · release_groom_queue 2 · close_finished_epics 4 · promote_ready 13 · hand_built_to_review 7 · report_break_glass 1 · report_rereview_missing 2 · total 42 over 11 phase(s) |
| 2026-10-05 08:45 PT | [37335425691](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37335425691) | refresh_stale_merge_refs 3 · flag_unlanded_work 6 · recover_limit_deaths 2 · flag_stranded 1 · serve_planner_line 1 · release_groom_queue 2 · close_finished_epics 4 · promote_ready 6 · report_break_glass 1 · report_rereview_missing 6 · total 32 over 10 phase(s) |
| 2026-10-05 09:06 PT | [37338159319](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37338159319) | refresh_stale_merge_refs 4 · flag_unlanded_work 5 · recover_limit_deaths 2 · flag_stranded 1 · serve_planner_line 1 · release_groom_queue 2 · close_finished_epics 4 · report_break_glass 1 · report_rereview_missing 6 · total 26 over 9 phase(s) |

Totals 26, 26, 42, 32, 26 — 30.4 a pass (estimated). The door was on
(`read-door: served 3 unknown 0 unavailable 0 (mode on)` on four of the five).

### Before DRE-5850 — the last five full passes before 2026-10-06 09:47 PT

| Pass | Run | `sweep-spend:` lines |
| --- | --- | --- |
| 2026-10-06 08:25 PT | [37487362254](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37487362254) | refresh_stale_merge_refs 1 · flag_unlanded_work 5 · flag_stranded 1 · release_groom_queue 2 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 2 · report_break_glass 1 · report_rereview_missing 5 · total 21 over 10 phase(s) |
| 2026-10-06 08:37 PT | [37489058802](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37489058802) | sweep_idle 1 · refresh_stale_merge_refs 1 · flag_unlanded_work 5 · serve_planner_line 1 · release_groom_queue 2 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 6 · report_break_glass 1 · report_rereview_missing 5 · total 26 over 11 phase(s) |
| 2026-10-06 08:49 PT | [37490772817](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37490772817) | sweep_idle 1 · refresh_stale_merge_refs 1 · flag_unlanded_work 5 · serve_planner_line 1 · release_groom_queue 2 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 9 · report_break_glass 1 · report_rereview_missing 2 · total 26 over 11 phase(s) |
| 2026-10-06 09:06 PT | [37493015762](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37493015762) | refresh_stale_merge_refs 1 · flag_unlanded_work 3 · flag_stranded 1 · release_groom_queue 19 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 13 · report_break_glass 1 · report_rereview_missing 2 · total 44 over 10 phase(s) |
| 2026-10-06 09:30 PT | [37496212185](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37496212185) | flag_unlanded_work 3 · flag_stranded 1 · release_groom_queue 14 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 13 · report_break_glass 1 · report_rereview_missing 2 · total 38 over 9 phase(s) |

Totals 21, 26, 26, 44, 38 — 31.0 a pass (estimated); `release_groom_queue`
19 and 14 at 09:06 and 09:30 PT are a groomer batch, other work.

### After — the five most recent full passes, 2026-10-07 07:28–08:36 PT

On the final code: DRE-5850 merged the day before, the door first served the long
threads at 2026-10-06 18:09 PT, and DRE-5861 (closed 08:36 PT, 20 seconds
after the last of these started) wrote nothing, its dry run having selected 0
cards.

| Pass | Run | `sweep-spend:` lines |
| --- | --- | --- |
| 2026-10-07 07:28 PT | [37636957025](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37636957025) | flag_unlanded_work 3 · flag_stranded 1 · report_intake_blockers 3 · release_groom_queue 2 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 17 · report_break_glass 1 · total 31 over 9 phase(s) |
| 2026-10-07 07:44 PT | [37639240627](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37639240627) | sweep_idle 1 · refresh_stale_merge_refs 2 · flag_unlanded_work 5 · report_intake_blockers 3 · serve_planner_line 1 · release_groom_queue 2 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 6 · report_break_glass 1 · report_rereview_missing 4 · total 29 over 12 phase(s) |
| 2026-10-07 07:53 PT | [37640573347](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37640573347) | sweep_idle 1 · refresh_stale_merge_refs 2 · flag_unlanded_work 3 · flag_stranded 1 · report_intake_blockers 3 · release_groom_queue 2 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 1 · report_break_glass 1 · total 18 over 11 phase(s) |
| 2026-10-07 08:05 PT | [37642136699](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37642136699) | sweep_idle 1 · refresh_stale_merge_refs 1 · flag_unlanded_work 3 · flag_stranded 1 · report_intake_blockers 3 · release_groom_queue 12 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 1 · report_break_glass 1 · total 27 over 11 phase(s) |
| 2026-10-07 08:36 PT | [37645250304](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37645250304) | sweep_idle 1 · refresh_stale_merge_refs 1 · flag_unlanded_work 5 · fix_approved_but_red 1 · settle_repair_cards 4 · report_intake_blockers 3 · serve_planner_line 1 · release_groom_queue 36 · close_finished_epics 2 · tend_epic_queue 1 · start_queued_epics 1 · promote_ready 13 · report_break_glass 1 · report_rereview_missing 4 · total 74 over 14 phase(s) |

Totals 31, 29, 18, 27, 74 — 35.8 a pass (estimated). Of the 74 at 08:36 PT,
41 are other work: `release_groom_queue` 36, `settle_repair_cards` 4,
`fix_approved_but_red` 1. Without the three named other-work phases and
DRE-4152's 3, the five after passes spent 26, 24, 13, 12, 30.

**The split lines, verbatim, after:**

| Pass | DRE-5847 | DRE-5850 | Door, that pass |
| --- | --- | --- | --- |
| 07:28 PT | `read-door: unlanded card lanes unknown (missing-field: the door answered UNKNOWN) — the 4 card(s) are read from Linear this pass` / `unlanded: card lanes — 0 from the door in one read, 3 read from Linear (the door could not answer them)` | `promotion: epic threads — 2 from the door in one read, 0 read from Linear (none)` | `read-door: served 7 unknown 1 unavailable 0 (mode on)` |
| 07:44 PT | `read-door: unlanded card lanes unknown (stale: the door answered UNKNOWN) — the 4 card(s) are read from Linear this pass` / `unlanded: card lanes — 0 from the door in one read, 3 read from Linear (the door could not answer them)` | `promotion: epic threads — 0 from the door in one read, 2 read from Linear (stale)` | `read-door: served 0 unknown 6 unavailable 0 (mode on)` |
| 07:53 PT | `read-door: unlanded card lanes unknown (missing-field: the door answered UNKNOWN) — the 4 card(s) are read from Linear this pass` / `unlanded: card lanes — 0 from the door in one read, 3 read from Linear (the door could not answer them)` | `promotion: epic threads — 2 from the door in one read, 0 read from Linear (none)` | `read-door: served 5 unknown 2 unavailable 0 (mode on)` |
| 08:05 PT | `read-door: unlanded card lanes unknown (missing-field: the door answered UNKNOWN) — the 4 card(s) are read from Linear this pass` / `unlanded: card lanes — 0 from the door in one read, 3 read from Linear (the door could not answer them)` | `promotion: epic threads — 2 from the door in one read, 0 read from Linear (none)` | `read-door: served 6 unknown 2 unavailable 0 (mode on)` |
| 08:36 PT | `read-door: unlanded card lanes unknown (stale: the door answered UNKNOWN) — the 4 card(s) are read from Linear this pass` / `unlanded: card lanes — 0 from the door in one read, 3 read from Linear (the door could not answer them)` | `promotion: epic threads — 0 from the door in one read, 2 read from Linear (stale)` | `read-door: served 1 unknown 6 unavailable 0 (mode on)` |

The door answered `stale` for every read on 2 of the 5 after passes (07:44
and 08:36 PT). Over the window a stale answer appears on 44 of the 163 full passes
that used the door, in bursts (measured); why it goes stale is not this
epic's and not observed here.

## The fleet key's hour and the per-job trailers

**The fleet key's hour — not observed.** The 650 an hour before was the whole
fleet key, every repo. `check_linear_budget.py` adds every repo's run logs,
and as the read-only identity it printed, at 2026-10-07 08:42 PT:

```
bureau-pipeline      Reconcile                            4     129       81
bureau-pipeline      Agent Plan [planner-oauth]           4     120       38
bureau-pipeline      Groomer                              1      94       94
…
agent-bureau         UNKNOWN                          UNKNOWN UNKNOWN  UNKNOWN
portico              UNKNOWN                          UNKNOWN UNKNOWN  UNKNOWN
TOTAL                                                    30     387   (+ 5 repo(s) UNKNOWN)   (73 run(s) skipped: log not available)
```

bureau-pipeline's own hour, 07:42–08:42 PT, was 387 requests, 267 of them on
the fleet key (measured). It is one repo of six and a morning hour, so it is
not the number the card asks for, and it is not compared to the 650.

**The `linear-calls:` trailers** (measured; summed per run over distinct
process tokens, the way `check_linear_budget.py` reads them; not changed by
this epic — neither job reads the lanes the epic moved):

| Job | Before | After |
| --- | --- | --- |
| Planner run (Agent Plan) | 39 requests, `budget: planner-oauth`, DRE-5807, 2026-10-04 14:08 PT, [37234906569](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37234906569) | 38 requests, `budget: planner-oauth`, DRE-6018, 2026-10-07 07:22 PT, [37636145451](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37636145451) |
| Build run (Agent Task) | 14 requests, `budget: fleet`, DRE-5801, 2026-10-04 14:08 PT, [37234871593](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37234871593) | 14 requests, `budget: fleet`, DRE-6018, 2026-10-07 07:29 PT, [37637143622](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37637143622) |

## What would close the open rows

- **agent-bureau and Portico.** A read-only token that reaches both repos, or
  an operator reading their sweep logs by hand, fills rows 1–3's other halves.
  The 10-04 baseline (30, 47, 35, 35, 40 a pass, 15:58–16:47 PT) is
  agent-bureau's and was not re-read here.
- **The fleet key's hour.** The same token, read during a busy afternoon
  hour (13:30–16:50 PT), with `check_linear_budget.py --hours 1`.
- **DRE-5847.** The door holding finished cards, or the watchdog asking only
  for cards it can hold — a mid-epic addition against DRE-5852.
