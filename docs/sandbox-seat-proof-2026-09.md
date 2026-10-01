# Three Linear seats, the sandbox's own hour, and a sandbox sweep that holds its hands off — observed 2026-10-01

The proof for [DRE-3636](https://linear.app/dreadnoughtfoundry/issue/DRE-3636),
under epic [DRE-3622](https://linear.app/dreadnoughtfoundry/issue/DRE-3622).
Every time below is Pacific (PDT, UTC−7) and comes from the event's own clock:
a run's `run_started_at`, a log line's timestamp, a Linear `createdAt`, or the
moment a command ran.

**The filename says 2026-09 and the observation is 2026-10-01.** The card was
written on 2026-09-12 and names this path; the fence it proves (DRE-3632)
merged on 2026-10-01 at 08:15:43 PT, so that is the day the observation could
first be made. The path is kept so the card's link resolves.

**Summary: 11 observations — 9 MET, 2 NOT MET, 0 NOT OBSERVED.**

| # | observation | verdict |
| -- | -- | -- |
| 1 | the identity check prints four `[OK]` lines on three distinct, non-admin users, exit 0 | **MET** (§1) |
| 2 | a sandbox sweep's `linear-budget:` line ends `budget: sandbox` | **MET** (§2.1) |
| 3 | the sandbox merge gate for the probe PR carries no `linear-budget:` line, and its log shows the card was empty | **MET** (§2.2) |
| 4 | the probe PR's CI run makes no Linear call | **MET** (§2.3) |
| 5 | the fleet key's `x-ratelimit-requests-remaining`, read immediately before and after one hand-dispatched sandbox sweep | **MET, with a caveat** (§2.4) — the fleet count did not fall (270 → 284), but a rolling refill could hide a small spend |
| 6 | three consecutive sandbox sweeps print eight `off-rail:` lines each, none of the eight phases' action lines, exit 0 | **MET** (§3.1) |
| 7 | a production sweep on the same day prints no `off-rail:` line | **MET** (§3.2) |
| 8 | each of the eight phases is accounted for in that production sweep's log, by a `sweep-spend:` line or the phase's own quiet line | **NOT MET as written** (§3.2) — four of the eight phases print nothing when idle, by code |
| 9 | the `bureau-sandbox` member's 24-hour board history: zero lane changes, zero comments, zero label changes on any card that is not the harness's own | **NOT MET** (§4) — 0 lane changes, **3 comments, 4 label changes**, all on `repo:portico` cards |
| 10 | the seat's writes on the harness's own cards in the same 24 hours, listed | **MET** (§4) — none in the window |
| 11 | the harness driver's one read named as deferred to DRE-3650 | **MET** (§5) |

**Two findings, and they must not be read as one.**

1. **The sandbox SWEEP held its hands off.** Since the fence reached
   `bureau-harness` at 08:26:48 PT, every one of its 17 completed scheduled
   sweeps observed (08:26 to 12:34 PT) printed exactly eight `off-rail:` lines,
   one per fenced phase, and no line any of those phases prints when it acts.
   The one sweep before the fence, at 08:04:41 PT, still ran the stranded
   watchdog, the reviewer-outage check and the planner line.
2. **The sandbox SEAT did not stay off the board.** Linear's own history shows
   `bureau-sandbox` adding the `hand-built` label four times, commenting three
   times, editing descriptions, changing relations and creating cards on
   production `repo:portico` cards in the 24 hours ending 12:28 PT. None of the
   writes checked lines up with a sandbox sweep (§4.3), and the comment texts
   are routing verdicts a person or an assistant session writes, not anything a
   sweep prints. What WAS observed is that the workstation's user-scope
   `linear` MCP server answers `me` as `bureau-sandbox` (§4.3). The sandbox
   seat — the Linear user, and so its hour — is being used by interactive
   sessions as well as by the harness.

---

## 1. Three seats, held apart — 12:23:28 PT

`scripts/check_linear_identities.py check`, taken from bureau-pipeline `main`
at `71b9925` (the script, `scripts/linear_ops.py`, the modules it imports and
`config/`, read through the GitHub API into a scratch directory — no clone),
with all four key homes exported in-process: `LINEAR_API_KEY_FLEET`,
`LINEAR_API_KEY` and `LINEAR_API_KEY_SANDBOX` from the operator's `.env`, and
`LINEAR_API_KEY_RELAY` read out of Secrets Manager `bureau/relay/linear-api-key`
in-process, never to a file or a terminal:

```
  [OK] fleet: LINEAR_API_KEY_FLEET resolves to 'Agent-Bureau' (id cebc4c53-fad2-410f-be31-f920b6ad773f), not an admin
  [OK] fleet/relay: LINEAR_API_KEY_RELAY resolves to 'Agent-Bureau' (id cebc4c53-fad2-410f-be31-f920b6ad773f), not an admin
  [OK] operator-tools: LINEAR_API_KEY resolves to 'bureau-tools' (id 0913a8db-839c-42d0-b2e8-70eda1b64617), not an admin
  [OK] sandbox: LINEAR_API_KEY_SANDBOX resolves to 'bureau-sandbox' (id e1ce0a23-74c8-4255-b0fd-20a0bf301b32), not an admin
4 key homes declared; 0 failed, 0 unknown
```

Exit 0. Four `[OK]` lines on three distinct user ids, none an admin.

### Which copy of the sandbox key that proves

The check read the operator's `.env` copy of the sandbox key. The two copies
the workflows use are GitHub Actions secrets, which cannot be read back, so
the check cannot look at them directly. What can be read:

| fact | reading |
| -- | -- |
| `bureau-harness` secret `LINEAR_API_KEY` last updated | 2026-09-13 15:17:40 PT |
| `bureau-pipeline` secret `LINEAR_API_KEY_SANDBOX` last updated | 2026-09-13 15:17:41 PT |
| the operator's `.env` last modified | 2026-09-13 15:17:40 PT |
| `agent-bureau` secret `LINEAR_API_KEY` (the fleet's, untouched by the fan-out) last updated | 2026-09-03 20:39:35 PT |

The three sandbox homes were written in the same two seconds. The live tie
between the harness's copy and the `bureau-sandbox` user is §2: the harness
sweep's own requests come off the bucket the `bureau-sandbox` key reads, not
the fleet's.

## 2. The sandbox spends its own hour

### 2.1 The sandbox sweep's budget line

Every scheduled `bureau-harness` reconcile run today ends with a budget line
naming the sandbox. Run
[36899128783](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36899128783),
started 10:24:21 PT, line timestamped 10:25:02 PT:

```
linear-budget: 2499 → 2494 (spent 5 this run (refilled mid-run); window resets 11:25 PT; budget: sandbox)
```

The stub (`bureau-harness/.github/workflows/reconcile.yml`) passes
`linear_identity: sandbox`, and the run's own environment echo reads
`LINEAR_IDENTITY: sandbox`. Two honest notes on what that line proves:

* The word `sandbox` is the identity the run DECLARED, not one it resolved.
  The proof that the run spent the sandbox user's bucket is the number: it
  started at 2,499 of 2,500 while the fleet bucket sat in the low hundreds at
  the same hour (§2.4, and agent-bureau's sweep at 12:03:36 PT read
  `95 → 19 … budget: fleet`). Two buckets that far apart at one moment are two
  users.
* The same `2499 → 249x` shape is on all 18 harness sweeps read for this
  record (08:04 to 12:34 PT), each spending 4 to 7 requests.

### 2.2 The sandbox merge gate makes no Linear request for a probe PR

The harness's probe pull request
[#2915](https://github.com/dreadnought-foundry/bureau-harness/pull/2915)
(`test(harness): live-rail probe main-gha-36908561415-1`, branch
`agent/harness-main-gha-36908561415-1-bot_pr_flow`) was merged by Merge Gate run
[36909495496](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36909495496),
started 11:47:47 PT. The gate derives the card from the branch name and only
then talks to Linear:

```
CARD=$(printf '%s' "$BRANCH" | grep -oiE 'DRE-[0-9]+' | head -1 | tr '[:lower:]' '[:upper:]' || true)
if [ -n "$CARD" ]; then echo "bureau-card: $CARD"; fi
…
[ -n "$CARD" ] && python3 .bureau-pipeline/scripts/linear_ops.py advance "$CARD" "In Review" "In Progress" || true
…
[ -n "$CARD" ] && python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \
```

The branch carries no `DRE-<n>`, so `CARD` was empty. The log shows it: the
`echo "bureau-card: $CARD"` line appears only as the script's own text and
never as runtime output, and the run's output goes straight from the decision
to the merge:

```
11:48:05 PT  decision=merge
11:48:05 PT  reason=CI green + critic APPROVE bound to 2621e23d56c11d40e7e2b18b72a56cfde9175784 — merge as qa-bot
11:48:10 PT  merged PR #2915
```

No `linear-budget:` line anywhere in the log, and no `linear_ops` output. The
key is present in the step's environment (`LINEAR_API_KEY: ***`) and unused.
The gate's earlier run on the same PR,
[36909378161](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36909378161)
at 11:46:51 PT (`stacked_prs: … stacked under #2915: none`, then
`decision=wait`, `reason=no critic verdict yet — wait`), carries no
`linear-budget:` line either.

### 2.3 The probe PR's CI makes no Linear call

CI run
[36909337987](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36909337987),
`pull_request` on the same branch, started 11:46:32 PT, success. The whole
workflow (`bureau-harness/.github/workflows/ci.yml`, comments stripped):

```
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]" && pytest -q
```

There is no `secrets:` or `env:` block, so no Linear key reaches the job. The
log's only test output is `5 passed, 128 warnings in 0.05s`, and the word
"linear" does not appear in the log at all.

### 2.4 The fleet header around a hand-dispatched sandbox sweep — 12:45:59 to 12:47:17 PT

The card asks for the fleet key's `x-ratelimit-requests-remaining` read
immediately before and after one `bureau-harness` reconcile run **dispatched by
hand**, "showing the sandbox run did not lower it, with the reading's own
drift from other traffic stated honestly."

**Who made this observation.** The session that wrote the rest of this record
was not permitted to dispatch a workflow run. The coordinating session ran the
dispatch and took the header readings after the CEO's go, which reached this
record as relayed: "yes to … 3636", 2026-10-01, about 12:50 PT. That time is
about four minutes after the dispatch (12:46:08 PT); both times are written
here as given. The header readings below are the coordinator's, made with the
fleet key read out of Secrets Manager `bureau/relay/linear-api-key` (viewer
`Agent-Bureau`), from `x-ratelimit-requests-remaining` on a `{ viewer { name } }`
query, with the key never printed. They are past readings and could not be
re-taken; the run itself was re-read from its own log for this record.

| when (PT) | fleet `x-ratelimit-requests-remaining` | note |
| -- | -- | -- |
| 12:45:59 | 271 / 2500 | window reset named 13:45:59 PT |
| 12:46:05 | **270** / 2500 | read just before the dispatch |
| 12:46:08 | — | `gh workflow run reconcile.yml -R dreadnought-foundry/bureau-harness` |
| 12:47:17 | **284** / 2500 | read as soon as the run completed |

The dispatched run is
[36916708263](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36916708263):
event `workflow_dispatch`, started 12:46:08 PT, ended 12:47:14 PT, success.
Its log, re-read for this record, has exactly eight `off-rail:` lines (all
eight phases named, no forbidden line), and ends:

```
linear-budget: 2499 → 2494 (spent 5 this run (refilled mid-run); window resets 13:47 PT; budget: sandbox)
```

Its scope line reads `sweep-scope: full pass (schedule)` although the run was
dispatched by hand; the label does not follow the trigger, and the pass is a
full one either way. A **scheduled** harness sweep also overlapped the bracket:
[36916761418](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36916761418),
12:46:34 to 12:47:49 PT, success, eight `off-rail:` lines,
`linear-budget: 2499 → 2493 (spent 6 this run (refilled mid-run); window resets 13:47 PT; budget: sandbox)`.
The 12:47:17 PT reading was taken while it was still running. Between them the
two sandbox sweeps spent 11 requests, all on the sandbox's bucket.

**What the bracket shows.** The fleet's count rose by 14 across the run,
270 → 284. Its hour is rolling, so it refilled while other fleet traffic ran,
and the reading cannot prove the fleet bucket was untouched to the request: a
refill of that size could hide a small fleet spend. What it does show is that
the fleet bucket did not drop by the sweep's 5 requests (or by the 11 the two
overlapping sweeps spent), and the sweep itself reports `budget: sandbox` from
a bucket that started at 2,499 while the fleet's stood at 270.

**Verdict: MET, with that caveat.** The card's words are "did not lower it,
with the reading's own drift … stated honestly," and the fleet count did not
fall, with its drift stated; the caveat stays because a rise across a rolling
window is consistent with "not lowered," not proof of "zero fleet requests,"
which only the sweep's own `budget: sandbox` line from a separate 2,499 bucket
carries.

#### An earlier substitute reading, around a scheduled sweep

Before the dispatch was approved, the same headers were read around the next
scheduled harness sweep, by this record's session:

| when (PT) | key | user | `x-ratelimit-requests-remaining` |
| -- | -- | -- | -- |
| 12:33:44 | fleet | Agent-Bureau | 285 / 2500 (baseline) |
| 12:33:45 | sandbox | bureau-sandbox | 2499 / 2500 (baseline) |
| 12:34:40 | fleet | Agent-Bureau | **277** / 2500 — the sweep below had just been queued (12:34:35) |
| 12:34:40 | sandbox | bureau-sandbox | 2499 / 2500 |
| 12:35:22 | fleet | Agent-Bureau | **220** / 2500 — the sweep had just completed |
| 12:35:23 | sandbox | bureau-sandbox | 2498 / 2500 |

The sweep was run
[36915295301](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36915295301),
scheduled, 12:34:35 to 12:35:22 PT, success, eight `off-rail:` lines, ending
`linear-budget: 2499 → 2493 (spent 6 this run (refilled mid-run); window resets 13:35 PT; budget: sandbox)`.

**The fleet reading fell by 57 across those 42 seconds, so it cannot show
what the card asks it to show.** Other fleet traffic moved it, and this record
cannot name whose: the only Actions runs in agent-bureau, bureau-pipeline,
portico and agent-bureau-demo in flight during the bracket were two one-second
Pipeline Medic runs in bureau-pipeline (12:34:49 and 12:35:14 PT), and the
fleet key is also spent by the console backend and the relay, which leave no
Actions log. The repos atlas, vericorr, deltasolv and project-template were
not checked. Each header read costs its own user one request, so this table
spent three fleet and three sandbox requests.

What the readings do show is the two buckets side by side: in the same
minute the fleet stood at 277 → 220 and the sandbox at 2,499 → 2,498, and the
sweep's own line says it spent 6 from a bucket that started at 2,499, so
its own accounting puts the spend on the sandbox. That is the §2.1
argument again, not the bracket the card specifies, which is why the
hand-dispatched bracket above was taken.

## 3. The sandbox sweep holds its hands off

### 3.1 Eight `off-rail:` lines on every pass since the fence arrived

`bureau-harness`'s stub rides bureau-pipeline `@main` (it is the one repo that
stays on `main`, by standing decision). PR
[#621](https://github.com/dreadnought-foundry/bureau-pipeline/pull/621)
(DRE-3632) merged at 08:15:43 PT as `1704fc3`. The sweep before it and the
first sweep after it:

| run | started (PT) | bureau-pipeline commit | `off-rail:` lines |
| -- | -- | -- | -- |
| [36881434103](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36881434103) | 08:04:41 | `db6adf6` (4 behind the fence) | **0** |
| [36884317280](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36884317280) | 08:26:48 | `1704fc3` (the fence's merge commit) | **8** |

The pre-fence sweep at 08:04:41 PT still ran three of the eight phases on the
sandbox seat — it printed `fleet-reviewer-outage: nothing to report — 0
could-not-run in the last 30 min`, two `watchdog:` lines (DRE-5408 and
DRE-5367), and `sweep-spend: serve_planner_line 1 request(s)`. None of those
lines appear in any sweep after it.

Every completed scheduled sweep after the fence, read from its log
(`gh run view <id> --log`):

| run | started (PT) | `off-rail:` | the eight phases named | forbidden lines | conclusion | `linear-budget:` |
| -- | -- | -- | -- | -- | -- | -- |
| [36884317280](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36884317280) | 08:26:48 | 8 | all eight | 0 | success | `2499 → 2493 … budget: sandbox` |
| [36886086153](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36886086153) | 08:40:18 | 8 | all eight | 0 | success | `2499 → 2495 … budget: sandbox` |
| [36887355366](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36887355366) | 08:50:12 | 8 | all eight | 0 | success | `2499 → 2494 … budget: sandbox` |
| [36889259830](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36889259830) | 09:04:52 | 8 | all eight | 0 | success | `2499 → 2493 … budget: sandbox` |
| [36892086417](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36892086417) | 09:27:07 | 8 | all eight | 0 | success | `2499 → 2495 … budget: sandbox` |
| [36893708000](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36893708000) | 09:40:05 | 8 | all eight | 0 | success | `2499 → 2494 … budget: sandbox` |
| [36894838200](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36894838200) | 09:49:15 | 8 | all eight | 0 | success | `2499 → 2495 … budget: sandbox` |
| [36896664376](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36896664376) | 10:04:19 | 8 | all eight | 0 | success | `2499 → 2493 … budget: sandbox` |
| [36899128783](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36899128783) | 10:24:21 | 8 | all eight | 0 | success | `2499 → 2494 … budget: sandbox` |
| [36900588496](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36900588496) | 10:36:10 | 8 | all eight | 0 | success | `2499 → 2494 … budget: sandbox` |
| [36902219620](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36902219620) | 10:49:21 | 8 | all eight | 0 | success | `2499 → 2492 … budget: sandbox` |
| [36904398438](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36904398438) | 11:06:50 | 8 | all eight | 0 | success | `2499 → 2493 … budget: sandbox` |
| [36907641853](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36907641853) | 11:32:54 | 8 | all eight | 0 | success | `2499 → 2493 … budget: sandbox` |
| [36910104943](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36910104943) | 11:52:38 | 8 | all eight | 0 | success | `2499 → 2494 … budget: sandbox` |
| [36911496363](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36911496363) | 12:03:45 | 8 | all eight | 0 | success | `2499 → 2493 … budget: sandbox` |
| [36913947331](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36913947331) | 12:23:38 | 8 | all eight | 0 | success | `2499 → 2493 … budget: sandbox` |
| [36915295301](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36915295301) | 12:34:35 | 8 | all eight | 0 | success | `2499 → 2493 … budget: sandbox` |

"Forbidden lines" counts, outside the `off-rail:` lines themselves, any line
opening `drain:`, `fleet-reviewer-outage:`, `urgent-fast-path:`,
`planner line:` or `epic-not-todo:`, any line containing `watchdog:` or
`escalat`, and any `sweep-spend:` line naming one of the eight phases. Every
pass was a full one (`sweep-scope: full pass (schedule)`), and every
conclusion is `success`, which is the workflow's exit 0.

The card asks for three consecutive passes; the three it was first audited on
are 09:49:15, 10:04:19 and 10:24:21 PT, re-read here, and the latest three are
11:52:38, 12:03:45 and 12:23:38 PT. The full pass at 10:24:21 PT, from the scope
line to the budget line, verbatim except that four repeats of the `busy-guard:`
line are shortened:

```
sweep-scope: full pass (schedule)
off-rail: 'bureau-harness' is not on the routing rail (config/repo-map.json), so this sweep writes to no board card it does not own — skipped drain_retiring_lanes, which would have moved every card out of a retiring lane, whoever owns it
busy-guard: agent-fix.yml is not on dreadnought-foundry/bureau-harness's default branch — this repo has no such stub, so no run of it can be in flight. Nothing to check, and nothing to report.
sweep-spend: flag_unlanded_work 4 request(s)
busy-guard: … (three more identical lines)
off-rail: 'bureau-harness' is not on the routing rail (config/repo-map.json), so this sweep writes to no board card it does not own — skipped recover_limit_deaths, which would have re-entered the stage a limit death left on unlabeled cards
busy-guard: … (one more identical line)
dependabot cards: 'bureau-harness' is not a repo on the repo map — no card is filed here (the harness sandbox runs this sweep too)
off-rail: 'bureau-harness' is not on the routing rail (config/repo-map.json), so this sweep writes to no board card it does not own — skipped report_fleet_reviewer_outage, which would have appended to or closed the fleet's one reviewer-outage card
off-rail: 'bureau-harness' is not on the routing rail (config/repo-map.json), so this sweep writes to no board card it does not own — skipped flag_stranded, which would have labeled off-rail cards and escalated stalled unlabeled Planning cards
intake-depth: 174 cards waiting in Intake, oldest 23.6 days — nothing leaves this lane on age; the groomer's approved batch is the way out, and the Urgent fast path for a card raised to Urgent since it shipped
off-rail: 'bureau-harness' is not on the routing rail (config/repo-map.json), so this sweep writes to no board card it does not own — skipped advance_urgent_intake, which would have moved Urgent Intake cards to Planning board-wide
off-rail: 'bureau-harness' is not on the routing rail (config/repo-map.json), so this sweep writes to no board card it does not own — skipped repair_frozen_planning_holds, which would have escalated and un-held unlabeled Planning cards our watchdog froze
off-rail: 'bureau-harness' is not on the routing rail (config/repo-map.json), so this sweep writes to no board card it does not own — skipped serve_planner_line, which would have released expired planner-slot claims on any repo's card
off-rail: 'bureau-harness' is not on the routing rail (config/repo-map.json), so this sweep writes to no board card it does not own — skipped carry_epics_out_of_todo, which would have carried unlabeled epics out of Todo
promotion: WIP cap 4, read from this run's max_wip input
promotion: 0 card(s) promoted, 0 parentless one-off(s), 0 hand-built (nothing dispatched) (WIP 0+0/4)
sweep-spend: promote_ready 6 request(s)
break-glass: 0 bypass(es) recorded fleet-wide, 0 still owing the classification skipped. A rising number is a finding about the front door, not about the people using it.
sweep-spend: report_break_glass 1 request(s)
fix-concurrency: this repo carries no agent-fix stub — nothing to check.
sweep complete: 0 nudge(s)
sweep-spend: total 11 request(s) over 3 phase(s)
linear-budget: 2499 → 2494 (spent 5 this run (refilled mid-run); window resets 11:25 PT; budget: sandbox)
```

The three phases that still spend on the sandbox seat — `flag_unlanded_work`,
`promote_ready`, `report_break_glass` — are the ones DRE-3632 read as scoped to
this repo's own cards or as read-only, and the promotion line confirms it:
`0 card(s) promoted`.

### 3.2 The production side — agent-bureau, 12:03:36 PT

`reconcile.yml` on agent-bureau, run
[36911479583](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36911479583),
scheduled, started 12:03:36 PT, `pipeline_ref: stable`, success. **No
`off-rail:` line** anywhere in the log. Its budget line:
`linear-budget: 95 → 19 (spent 76 this run (refilled mid-run); window resets 13:05 PT; budget: fleet)`.
(bureau-pipeline's own `self-reconcile.yml` at 12:03:56 PT,
run [36911520084](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36911520084),
also prints no `off-rail:` line and ends `budget: fleet`.)

The eight phases, accounted for one by one in run 36911479583:

| phase | what the log shows | accounted by |
| -- | -- | -- |
| `drain_retiring_lanes` | nothing | silent by design while nothing is retiring: it prints only `drain: moved N card(s) …` when it moves a card |
| `recover_limit_deaths` | nothing | **silent when idle**: it prints only the lines `limit_recovery.recover` yields, and yielded none |
| `report_fleet_reviewer_outage` | `fleet-reviewer-outage: nothing to report — 0 could-not-run in the last 30 min` | its own quiet line |
| `flag_stranded` | 15 `watchdog:` lines, e.g. `watchdog: DRE-3636 is labeled 'hand-built' — no dispatched run is expected, so a missing run receipt and an off-rail repo are both normal here, not a strand`, and `watchdog: DRE-5418 is waiting for a planner slot (place 1 of 1, waited 7 minutes) — not a strand` | its own lines |
| `advance_urgent_intake` | `urgent-fast-path: no card in Intake has been raised to Urgent since 2026-10-01 00:00 PT — nothing to move` | its own quiet line |
| `repair_frozen_planning_holds` | nothing | **silent when idle**: with no frozen candidate it returns before printing (`if not candidates: return repaired`) |
| `serve_planner_line` | `planner line: 1 waiting, oldest 7 minutes, 2 running and 0 dispatched of 2` and `sweep-spend: serve_planner_line 1 request(s)` | its own line and a spend line |
| `carry_epics_out_of_todo` | nothing | **silent when idle**: it prints only `epic-not-todo: <id> found in Todo …` when it carries an epic |

So no `off-rail:` line was printed, which is observation 7, MET. Observation 8
is **NOT MET as the card words it**: the card expected each phase's own quiet
line where it did not spend, naming the repair's among them, and four of the
eight phases — the drain, the limit-death recovery, the frozen-hold repair and
the epic carry — print nothing at all when they have nothing to do. The card
itself already calls the drain silent by design, so the gap between the card
and the code is three phases: the recovery, the repair and the carry. They are
accounted for here by reading their print sites in `scripts/reconcile.py` on
`main`, not by a line in the log. That is a fact about the card's expectation,
not a failure of the fence: in the sandbox those same four phases print their
`off-rail:` line, so the sandbox side of each is in the log.

## 4. The board — `bureau-sandbox`'s 24 hours

### 4.1 How it was read

Linear's API, as the operator-tools user (`bureau-tools`), read-only:

* every issue updated in the window (`issues(filter: {updatedAt: {gt: …}},
  includeArchived: true)`, 401 issues in 17 pages), with each issue's newest
  50 history entries, kept where `actor.id` is `bureau-sandbox`'s id
  `e1ce0a23-74c8-4255-b0fd-20a0bf301b32`. No issue's 50 entries stopped inside
  the window, so nothing was cut off;
* every comment by that user (`comments(filter: {user: {id: {eq: …}}})`);
* every issue created by that user (`issues(filter: {creator: …})`).

**The window is 2026-09-30 12:28:02 PT to 2026-10-01 12:28:02 PT.**

### 4.2 The count

Every card below carries `repo:portico`. None is a harness card (a card whose
pull request lives in `bureau-harness`).

| kind | on the harness's own cards | on every other card |
| -- | -- | -- |
| lane changes | 0 | **0** |
| comments | 0 | **3** |
| label changes | 0 | **4** |

The comments:

| when (PT) | card | opens with |
| -- | -- | -- |
| 2026-09-30 18:02:30 | [DRE-5388](https://linear.app/dreadnoughtfoundry/issue/DRE-5388) | `🧭 routing-verdict: **WORKBENCH** — needs an interactive flow.` |
| 2026-09-30 18:09:39 | [DRE-5262](https://linear.app/dreadnoughtfoundry/issue/DRE-5262) | `🧭 routing-verdict: **WORKBENCH** — built by hand, superseding the FLEET verdict above.` |
| 2026-10-01 01:14:42 | [DRE-5392](https://linear.app/dreadnoughtfoundry/issue/DRE-5392) | `🧭 routing-verdict: **WORKBENCH** — built by hand.  The CEO chose on 2026-09-30 to fix this` |

The label changes, all one label added and none removed:

| when (PT) | card | added |
| -- | -- | -- |
| 2026-09-30 18:09:36 | [DRE-5262](https://linear.app/dreadnoughtfoundry/issue/DRE-5262) | `hand-built` |
| 2026-10-01 01:14:41 | [DRE-5392](https://linear.app/dreadnoughtfoundry/issue/DRE-5392) | `hand-built` |
| 2026-10-01 12:24:19 | [DRE-5389](https://linear.app/dreadnoughtfoundry/issue/DRE-5389) | `hand-built` |
| 2026-10-01 12:24:20 | [DRE-5401](https://linear.app/dreadnoughtfoundry/issue/DRE-5401) | `hand-built` |

**Observation 9 is NOT MET.** The expected answer on every card that is not
the harness's own was zero of each; it is zero lane changes, three comments and
four label changes.

The seat made other board writes in the same window that the card's three-way
count does not name. They are listed because they are writes:

* **cards created:** [DRE-5388](https://linear.app/dreadnoughtfoundry/issue/DRE-5388)
  (2026-09-30 17:35:40), [DRE-5392](https://linear.app/dreadnoughtfoundry/issue/DRE-5392)
  (17:40:22), [DRE-5402](https://linear.app/dreadnoughtfoundry/issue/DRE-5402)
  (18:09:08), [DRE-5403](https://linear.app/dreadnoughtfoundry/issue/DRE-5403)
  (18:09:25), all `repo:portico`;
* **description edits:** DRE-5257 (2026-09-30 15:39:26), DRE-5388 (17:40:16),
  DRE-5262 (18:25:56), DRE-5263 (18:25:57), DRE-5261 (18:26:06);
* **relation changes:** DRE-5257 and DRE-5210 (15:39:26–27), DRE-5388, DRE-2494
  and DRE-5392 (17:40:16–23), DRE-5274, DRE-5402, DRE-5209 and DRE-5403
  (18:09:09–44);
* **an attachment:** "Analysis and plan: Portico App Data (current link)" on
  DRE-5327 (2026-09-30 13:01:06).

Beyond the window, the seat's whole record of created cards was read: **107
cards** since 2026-09-23 10:20:45 PT (the first, DRE-4703) — 84 `repo:portico`,
12 `repo:agent-bureau`, 7 `repo:bureau-pipeline`, 4 `repo:vericorr`. The
latest two of them, DRE-5464 and DRE-5465, were created at 12:32:03 and
12:32:32 PT today, four minutes after the window closed. It is still
happening.

**Observation 10 is MET, and the list is empty.** The seat's legitimate writes
on the harness's own cards — a critic verdict, a merge note — would appear on a
card whose pull request lives in `bureau-harness`. Every harness PR in the
window was a card-less probe (§2.2), so the harness's own cards received
nothing from this seat in the 24 hours, and none was expected.

### 4.3 Where those writes came from — what was seen, and what was not

**Not from a sandbox sweep.** For each write moment, the `bureau-harness`
runs in flight were checked:

* 12:24:19–20 PT (the two label adds): the 12:23:38 PT sweep
  ([36913947331](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/36913947331))
  printed its last sweep line at 12:24:07 PT, names neither DRE-5389 nor
  DRE-5401 anywhere in its log, prints eight `off-rail:` lines, and reports
  `0 card(s) promoted`. No other harness run was created between 12:20 and
  12:25 PT.
* 01:14:41–42 PT (DRE-5392): the only harness run created between 01:05 and
  01:16 PT was the sweep that ran 01:06:49–01:07:33 PT, seven minutes earlier.
* 17:30 to 18:27 PT on 2026-09-30 (the comments on DRE-5388 and DRE-5262, the
  creations, and the 17:40 and 18:09 relation and description writes): harness
  merge gates, CI and critic runs were in flight between 17:54 and 18:03 PT,
  all on card-less probe branches, and one sweep ran 18:16:43 to 18:17:14 PT;
  the comment texts are routing verdicts ("built by hand", "the CEO chose on
  2026-09-30"), which no sweep, gate or CI step prints.
* The 13:01 PT attachment and the 15:39 PT writes on 2026-09-30 were not
  checked against harness runs.

**What was seen.** This workstation has two Linear MCP servers. Asked who "me"
is, at about 12:30 PT:

| MCP server | defined in | answers `me` as |
| -- | -- | -- |
| `linear` | `~/.claude.json`, user scope — every project on this machine | **`bureau-sandbox`** (id `e1ce0a23-…`), not an admin |
| `linear-server` | `~/.claude.json`, project scope for agent-bureau only | Frederick Conklin (`sid`), the workspace admin |

So any assistant session on this machine opened outside agent-bureau — a
portico session, for example — writes Linear as `bureau-sandbox` when it uses
the Linear MCP. That matches the `repo:portico` concentration and the
routing-verdict texts. The DRE-5392 comment (01:14:42 PT) was read in full: it
is free prose ("The CEO chose on 2026-09-30 to fix this as its own card after
DRE-5388 … Built on `agent/DRE-5392-image-shown-inline`."), with none of the
`**Why:** … **Where it goes:** … **Marked:**` template the pipeline's own
routing-verdict writer uses and no `📎 pipeline-act:` receipt, which points
away from a pipeline script holding the sandbox API key. It is an inference from those two facts; nothing here
saw a session make one of those writes. The record that would settle it is
Linear's own audit log for those API calls, which this session did not read.

What it means either way: **the sandbox seat is no longer the harness's
alone.** (The MCP server signs in over OAuth, not with the API key, but
Linear's limit is per user, so it spends the same hour.) Its bucket still read 2,499 of 2,500 at 12:34:40 PT, so nothing is
starving yet, but the premise that every `bureau-sandbox` write is a harness
write — the premise §4's count rests on — does not hold today.

## 5. Did the claim hold?

**The fence held and the seat did not stay clean, and they are not the same
sentence.** Every sandbox sweep since 08:26 PT skipped all eight fleet-wide
writers and said so; the seat it runs on was written to production cards by
something else.

One row per acceptance criterion of the five build cards. "PR CI" means the
check-run record of the merged pull request, which is what the criterion is
about; those rows were not re-run live here, and they are marked so rather
than counted as observations.

### DRE-3579 — the sandbox's sweeps stop moving production cards

| criterion | held? | evidence |
| -- | -- | -- |
| both stubs pass a dated `intake_hold`, and neither carries a `schedule:` trigger | **did not** | both stubs carry `schedule: - cron: "*/15 * * * *"` on `main` today. `bureau-harness` passes `intake_hold: ${{ vars.INTAKE_HOLD }}` and its variable reads `2026-09-09` (set 2026-09-09 20:50 PT). `agent-bureau-demo` passes the same expression but has **no** `INTAKE_HOLD` variable, so its hold is empty. The DRE-3636 card itself says DRE-3579 "did not remove the schedule" |
| a hand-run sandbox sweep after the edit escalates nothing and says so | **held**, on scheduled sweeps (no hand run here) | §3.1: the two escalating phases, `flag_stranded` and `repair_frozen_planning_holds`, print `off-rail:` lines and nothing else on 17 consecutive sweeps |
| one overnight with zero cards aged into Green Light by a sandbox sweep | **held** for the night of 2026-09-30/10-01 | §4.2: zero lane changes by `bureau-sandbox` in the 24 hours ending 12:28 PT; the Intake age-out itself was removed by DRE-4141 (`intake-depth: … nothing leaves this lane on age`) |

### DRE-3628 — sandbox is the third declared identity

| criterion | held? | evidence |
| -- | -- | -- |
| three identities declared; with no keys exported the check prints four `[UNKNOWN]` lines and exits non-zero, no `[FAIL] config:` | **held**, live at 12:32:46 PT | `[UNKNOWN] fleet`, `[UNKNOWN] fleet/relay`, `[UNKNOWN] operator-tools`, `[UNKNOWN] sandbox`, then `4 key homes declared; 0 failed, 4 unknown — unknown is not a pass`, exit 1 |
| `declared_identity_names()` returns the three names, so `LINEAR_IDENTITY=sandbox` prints `budget: sandbox` | **held** | every sandbox sweep's budget line ends `budget: sandbox` (§2.1) |
| fake-viewer test: sandbox on the fleet's user fails `must_differ_from`; three distinct users give four `[OK]` | **held** at merge (PR CI) | PR [#386](https://github.com/dreadnought-foundry/bureau-pipeline/pull/386), merged 2026-09-13 12:20:02 PT, `scripts unit tests` success; the live analogue is §1 |
| neither the script nor the JSON `_readme` still says two non-human identities | **held** | `main`: the docstring says "three non-human users are three separate budgets"; the `_readme` heads "WHY A SEAT EACH" |
| the three named test files are green | **held** at merge (PR CI) | PR #386 check runs all success |

### DRE-3630 — `reconcile.yml` takes `linear_identity`

| criterion | held? | evidence |
| -- | -- | -- |
| the input exists with default `fleet`; the `sweep` job's `env.LINEAR_IDENTITY` is exactly the input expression; no step sets it | **held** | `main` `.github/workflows/reconcile.yml`: input `linear_identity`, `type: string`, `required: false`, `default: "fleet"`; line 176 `LINEAR_IDENTITY: ${{ inputs.linear_identity }}` is the file's only `LINEAR_IDENTITY` |
| `tests/test_linear_identity_declared.py` updated as specified | **held** at merge (PR CI) | PR [#384](https://github.com/dreadnought-foundry/bureau-pipeline/pull/384), merged 2026-09-13 12:02:28 PT, `scripts unit tests` success |
| `check_reconcile_env.py` exits 0 and the named suites are green | **held** at merge (PR CI) | PR #384 check runs all success |
| `self-reconcile.yml` is unchanged and still resolves to `fleet` | **held** | `main` `self-reconcile.yml` passes no `linear_identity`; its 12:03:56 PT run ends `budget: fleet` |

### DRE-3632 — the off-rail fence

| criterion | held? | evidence |
| -- | -- | -- |
| `off_rail()` is true for `bureau-harness` and false for every repo-map key | **held** live for three slugs; the full iteration at merge (PR CI) | `bureau-harness` prints eight `off-rail:` lines (§3.1); agent-bureau and bureau-pipeline print none (§3.2); PR [#621](https://github.com/dreadnought-foundry/bureau-pipeline/pull/621) `scripts unit tests` success |
| a full `main()` over the fixture board prints exactly eight `off-rail:` lines and makes none of the named writes | **held**, live | 17 consecutive live passes, eight lines each, zero forbidden lines (§3.1) |
| per phase, run twice: under agent-bureau the write happens; under bureau-harness it is skipped | **held** at merge (PR CI) | PR #621 check runs; the production half's phases are in §3.2 |
| the named suites are green and unedited | **held** at merge (PR CI) | PR #621 `scripts unit tests` success |
| a test pins `OFF_RAIL_SKIPPED` to the eight names | **held** | `main` `scripts/reconcile.py` `OFF_RAIL_SKIPPED` holds exactly the eight names; the pinning test ran in PR #621's CI |
| `scripts/harness/README.md` says the zero-writes promise is held by the fence | **held** | `main` line 265: "Since DRE-3632 the sandbox's own sweep holds that promise mechanically: a sweep whose slug is off the routing rail skips its eight fleet-wide board writers (`reconcile.OFF_RAIL_SKIPPED`) and prints one `off-rail:` line for each." |
| `check_tdd_commits.py` exits 0 before the PR opens | **held** at merge (PR CI) | PR #621 `TDD commit discipline` success |

### DRE-3634 — the bureau-sandbox seat exists

| criterion | held? | evidence |
| -- | -- | -- |
| four `[OK]` lines on three distinct non-admin ids, exit 0 | **held**, live at 12:23:28 PT | §1 |
| `LINEAR_API_KEY_SANDBOX` in bureau-pipeline; `LINEAR_API_KEY` in bureau-harness updated on the day | **held** | secret metadata: 2026-09-13 15:17:41 PT and 15:17:40 PT (§1) |
| the next harness reconcile ends `budget: sandbox` | **held** (every run today) | §2.1; the first run after 2026-09-13 was not re-read |
| no product repo's `LINEAR_API_KEY` was changed on the day | **held** | agent-bureau's `LINEAR_API_KEY` last updated 2026-09-03 20:39:35 PT |

### Deferred to DRE-3650 — the harness driver's one read

| criterion | held? | evidence |
| -- | -- | -- |
| the harness driver (`harness.yml`) reads Linear on the sandbox seat | **deferred to [DRE-3650](https://linear.app/dreadnoughtfoundry/issue/DRE-3650)** — not yet true | `main` `.github/workflows/harness.yml`, step "Run harness scenarios": `LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}` — in bureau-pipeline that is the fleet key. DRE-3635, which would have moved it, is Canceled (2026-09-11) in favor of DRE-3651 under DRE-3650. Note that `config/linear-identities.json` already describes `LINEAR_API_KEY_SANDBOX` in bureau-pipeline as "read only by harness.yml"; on `main` nothing reads it yet |

## What this record says is owed

1. **The user-scope `linear` MCP server is signed in as `bureau-sandbox`.**
   Every assistant session on this workstation outside agent-bureau writes
   Linear as the sandbox seat, which is why §4's count is not zero. A person
   must re-authenticate that MCP server as the right user — `bureau-tools`, the
   operator-tools seat, is the one the identity declaration names for "the
   operator's own scripts and assistant sessions" — and then re-read the seat's
   24-hour history. Nothing here touched the MCP configuration.
2. **DRE-3579's first criterion is not true today.** Both sandbox stubs carry
   the `*/15` schedule, and `agent-bureau-demo` has no `INTAKE_HOLD` variable,
   so its stub passes an empty hold. With the fence on `main` the harness's
   schedule is now harmless to the board; the demo stub is not covered by this
   epic's fence (it stays on the fleet seat by DRE-3634's step 5), and whether
   its empty hold matters is a question for the epic that owns it.
3. **The identity declaration runs ahead of `main`.** `config/linear-identities.json`
   says `LINEAR_API_KEY_SANDBOX` is read by `harness.yml`; it is not, until
   DRE-3650's card lands.
