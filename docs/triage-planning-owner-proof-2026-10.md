# PROOF record — DRE-6483: PROOF: a card the planning step sends to Triage gets an owner and a next step — the bound exit read on DRE-4059's real thread, the hygiene lane naming the staged canary with its age and alarming once, the duplicate guard pinned, and the live checks green (epic DRE-6415)

**Status: FAIL.**

Four of the five judged rows are met on the released commit. On DRE-4059's real thread, the bound exit answers `rewrite`, and the three second-critic send-backs of 2026-10-08 are classed `uncertain`. The hygiene lane named the canary DRE-6562 with its age every hour. It alarmed on the canary once, and two later live passes stayed quiet. The duplicate guard's 2026-10-08 skip moved no lane, and its two tests pass in the released commit's own CI run. The four checks exit 0.

The row that is not met is the alarm's wording. The card says the receipt opens `held plan-critic-bound past the 8-hour bound in Triage ·`. The released code writes `held plan-critic-bound in Triage since 2026-10-10T04:29:35Z past the 8-hour bound ·`. The alarm fired exactly once, as the card asks, but its text differs from the card's contract, so the row is `Not met.` as written.

## How this was recorded

- Run: proof run 38062283564 — https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38062283564
- Release observed: the `stable` tag (this repo's one release surface, `pipeline-channel` in `.github/bureau/release.json`, `record: channel`) at `2b550bad409e6823d3efe09a9e7640f0a739f687`, the merge of #925 (DRE-6500). Read with `GH_TOKEN=$GH_READ_TOKEN gh api repos/dreadnought-foundry/bureau-pipeline/git/matching-refs/tags/`. `scripts/proof_release.py check` over the six sibling merges, run as the read token, printed:

  ```
  proof-release: ready — pipeline-channel (the whole repository): stable carries #884 (b218f8c) for DRE-6451
  proof-release: ready — pipeline-channel (the whole repository): stable carries #898 (8cf5053) for DRE-6452
  proof-release: ready — pipeline-channel (the whole repository): stable carries #895 (0e3a46b) for DRE-6454
  proof-release: ready — pipeline-channel (the whole repository): stable carries #908 (d0f34c1) for DRE-6455
  proof-release: ready — pipeline-channel (the whole repository): stable carries #911 (02eac7c) for DRE-6456
  proof-release: ready — pipeline-channel (the whole repository): stable carries #877 (9f86cf4) for DRE-6468
  ```

  DRE-4059's one-off cards are carried too: DRE-6359 (#866, `491e0100`) and DRE-6380 (#878, `adda475d`) are ancestors of `stable`. DRE-6469 is the operator's canary card and DRE-6481 has no commit in this repository, so neither has a merge here to carry.
- The release went live at **2026-10-10 02:13 PT**: promote-channel run 38040632728 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38040632728) logged GitHub's answer `{"ref":"refs/tags/stable",…,"object":{"sha":"2b550bad409e6823d3efe09a9e7640f0a739f687","type":"commit",…}}` at 09:13:46 UTC.
- Commit observed: every script below ran from a worktree of `2b550bad409e6823d3efe09a9e7640f0a739f687` (`git worktree add /tmp/released stable`), so each row reads the released code, not `main` (`main` was at `9c5b7d2`, 58 commits ahead, when this run started).
- Observation window: **2026-10-10 08:07 PT** (this run started reading) to **2026-10-10 08:13 PT** (its last read). Every read was made in this one sitting.
- The hygiene passes quoted in Rows 2 and 3 ran on `main` at the time of each pass, not on `stable`. `git diff --stat stable bae50d3b` and `git diff --stat stable 76babeda` over `scripts/hygiene_triage.py`, `scripts/hygiene.py` and `scripts/hold.py` are both empty, so the lane those passes ran is the released lane.
- Every time below is Pacific. Nothing was written to any card but this one's heartbeats and its actor marker.
- proof local run: none — no `Local screen:` row on this card.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** The step summary's line: `proof identity: github read — app agent-bureau-bot-2, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. Used for every GitHub read: the tags and the promote-channel run, the sibling pull requests, DRE-6468's pull request #877 and its files, the planner runs 37878064594 and 37878973834 and their logs, the Pipeline Tests run 38040174989 and its jobs and logs, and the eleven scheduled hygiene runs from 2026-10-09 21:48 PT to 2026-10-10 07:42 PT and their logs. `gh variable get HYGIENE_CARD` answered `HTTP 403: Resource not accessible by integration`, as the line above says it would, so the summary card's id was read off the hygiene runs' own logs instead (`HYGIENE_CARD: DRE-5774` in run 38060689517's job environment). The one refused write:

      GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs \
        -f ref=refs/heads/proof-write-probe-38062283564 -f sha=2b550bad409e6823d3efe09a9e7640f0a739f687

  GitHub's answer: `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}` — `gh: Resource not accessible by integration (HTTP 403)`. No ref was created.
- **GitHub, written: `GH_TOKEN`.** Used only to push `agent/DRE-6483-proof-record` and open this record's pull request.
- **Linear: `LINEAR_API_KEY`, the fleet key** — the pipeline's scripted read identity. Reads: DRE-4059's thread (through `plan_bound.py read` and `dump-comments --with-authors`) and its lane history, DRE-6562's thread, and DRE-5774's thread. Writes: this card's heartbeats and its actor marker, nothing else.
- **linear requests: 16 of 40** when this record was pushed. That total is summed off the `linear-calls:` lines each invocation printed, with the four heartbeats so far included. Two writes come after the push: the `⏳ 5/5 PR opened` heartbeat and the actor marker. Each heartbeat so far cost two requests, so the run ends near 20 of 40.
- **aws: none.** The step summary's line: `proof identity: aws — none, PROOF_ROLE_ARN not provided`. No row here needs one.
- **Scratch state: none created.**

## Criteria

| Criterion | Result |
| -- | -- |
| Live fact: `python3 scripts/plan_bound.py read DRE-4059 --stage post --whole-thread`, run against the real thread as the proof-reader identity on the released commit, is quoted whole in the record, and the row is judged on what the exit DECIDED: the outcome word is `rewrite` — DRE-4059 carries no `🔁 bound-rewrite:` receipt, and no finding on its thread classes `decision` — and among the classed lines are DRE-4059's three second-critic send-backs of 2026-10-08 (round 1 opening `DRE-6380's precheck reads only the card body`, round 2 opening `DRE-6360 claims the one-off critic and the planner will read`, round 3 opening `DRE-6358: the "you have not been asked anything on this card before" form`), each classed `uncertain` with a why. | Met. Read at 2026-10-10 08:08 PT on `2b550ba`, exit 0. The outcome word is `rewrite`. All six classed lines are `uncertain` and none is `decision`. The three 2026-10-08 second-critic rounds (19:39, 19:57 and 20:15 PT) are among them, each with a why. The thread has 98 comments and none carries `🔁 bound-rewrite`. Output quoted whole under Row 1. |
| Live fact: the hygiene agent's standing summary card (`HYGIENE_CARD`) carries a summary dated after the canary's stamp whose Triage section carries `left DRE-<canary> — held plan-critic-bound for <N> hours in Triage —` for the canary card named on this card, quoted with the pass time and run id, beside the canary's own stamp line read off its comments. | Met. The canary named on this card is DRE-6562, stamped 2026-10-09 21:29:35 PT. `HYGIENE_CARD` is DRE-5774. Its summary of 2026-10-10 05:52:25 PT (run 38053473320) carries `- left DRE-6562 — held plan-critic-bound for 8 hours in Triage — …`. Nine summaries from 21:50 PT on name it, counting 0 to 8 hours. Quoted under Row 2. |
| Live fact: the canary card carries exactly one receipt opening `🧹 hygiene: hyg-triage-aged — held plan-critic-bound past the 8-hour bound in Triage ·`, read off its comments, with at least two scheduled live hygiene passes (`dry_run=false` in each run's log) completed after the eighth hour since its stamp — the once; the record quotes the receipt with its PT time and lists the passes read with their run ids. | Not met. DRE-6562 carries exactly one `hyg-triage-aged` receipt, posted 2026-10-10 05:52:13 PT by run 38053473320. Three live passes completed after the eighth hour (05:29:35 PT): 38053473320, 38056726830 and 38060689517, each with `dry_run=false`. The two later passes each logged `suppressed: DRE-6562 hyg-triage-aged`. But the receipt opens `🧹 hygiene: hyg-triage-aged — held plan-critic-bound in Triage since 2026-10-10T04:29:35Z past the 8-hour bound ·`, not the expected `… — held plan-critic-bound past the 8-hour bound in Triage ·`. The released `scripts/hygiene_triage.py:344` writes that cause. Quoted under Row 3. |
| Live fact: on the released commit, `python3 scripts/hold.py check`, `python3 scripts/green_light_rows.py check`, `python3 scripts/pipeline_act.py check` and `python3 scripts/planning_escalation.py check` each exit 0, the four exit codes and the commit sha recorded. | Met. Run at 2026-10-10 08:09 PT from a worktree of `2b550bad409e6823d3efe09a9e7640f0a739f687`: `hold.py check` exit 0, `green_light_rows.py check` exit 0, `pipeline_act.py check` exit 0, `planning_escalation.py check` exit 0. Last lines quoted under Row 4. |
| Live fact: the duplicate-dispatch guard moved nothing — the record quotes DRE-4059's `🤖 Duplicate dispatch skipped … This run planned nothing and moved no lane` receipt of 2026-10-08 20:22 PT beside the lane history showing the one Triage entry at 20:22:22 PT came from the parking run, and names the two tests DRE-6468 added to `tests/test_plan_dispatch_dedupe.py` (the skip path writes no lane; every lane writer in `plan.yml` sits behind the guard's skip) with their result in the released commit's own CI run on `main`, read off the Actions log, not a run of the proof's own. | Met. The receipt was posted 2026-10-08 20:22:59 PT by run 37878973834. DRE-4059's lane history has one Triage entry: `Planning -> Triage` at 20:22:22 PT. Parking run 37878064594 logged `DRE-4059 → Triage` at that second. The duplicate run started at 20:22:26 PT, after the move. The two tests are `test_a_refused_plan_dispatch_writes_no_lane_and_says_so_once` and `test_every_lane_writer_in_the_planner_runs_behind_the_skip`. Both `PASSED` in Pipeline Tests run 38040174989 on `main` at `2b550ba`, which concluded `success`. Quoted under Row 5. |
| The record ends `**Status: PASS.**` only when every judged row is `Met.` or accepted; the merge gate holds it otherwise (DRE-6141). The CEO closes this card after reading the merged record; nothing here waits on him signing in. | Met. Row 3 is `Not met.`, so this record's status is `**Status: FAIL.**`, not PASS, and the gate holds it. No row needs the CEO's press, and nothing here waits on his sign-in. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## Row 1 — the bound exit on DRE-4059's real thread

Command, from the worktree of `2b550bad`, at 2026-10-10 08:08:04 PT:

    python3 scripts/plan_bound.py read DRE-4059 --stage post --whole-thread

Standard output, whole (tab-separated as printed):

```
rewrite
uncertain	uncertain — matches neither list; goes to the CEO as a decision	the epic's closing-line requirement ("whatever replaces it must differ when the finding differs") is carried by no card and not mentioned in the plan, though the identical sentence is still on main.
uncertain	uncertain — matches neither list; goes to the CEO as a decision	DRE-6380's precheck reads only the card body, so a one-off the exit would route by its label or title (agent:ops, no-code, a PROOF title) but that has no checklist gets sent to the rewrite and spends send-back rounds.
uncertain	uncertain — matches neither list; goes to the CEO as a decision	DRE-6360 claims the one-off critic and the planner will read the description the answer-writer just wrote, but both embed the dispatch payload's copy (`steps.card.outputs.description`), which was fixed before the writer ran.
uncertain	uncertain — matches neither list; goes to the CEO as a decision	DRE-6358: the "you have not been asked anything on this card before" form is read off `escalate()`'s default comment read, which is only the fifty newest comments, so a signed answer that has scrolled out of that window makes the note tell the CEO something false
uncertain	uncertain — matches neither list; goes to the CEO as a decision	DRE-6359, DRE-6380, DRE-6360 edit the same one-off exit code (`_cmd_decide`, `plan.yml`) as DRE-6415's DRE-6454, DRE-6455 and DRE-6456, and neither plan orders itself against the other.
uncertain	uncertain — matches neither list; goes to the CEO as a decision	DRE-6360 and DRE-6380 add permanent tests that pin the text of plan.yml steps DRE-6455 and DRE-6456 (epic DRE-6415) must change, so those two cards' CI goes red on tests they do not own.
```

Standard error (the Linear budget lines omitted):

```
DRE-4059: all 6 finding(s) are for the planner to answer, read on the post stage, and the card has not had its one bound rewrite
```

The three 2026-10-08 second-critic rounds, matched to the `plan-critic:` markers on DRE-4059's thread (`linear_ops.py dump-comments DRE-4059 --with-authors`, all `authored_by_pipeline: true`):

- 2026-10-08 19:39:22 PT — `plan-critic: stage=post round=1 result=SEND_BACK collisions=1 — DRE-6380's precheck reads only the card body, …` — the second classed line above.
- 2026-10-08 19:57:23 PT — `plan-critic: stage=post round=2 result=SEND_BACK collisions=1 open=0 — DRE-6360 claims the one-off critic and the planner will read …` — the third.
- 2026-10-08 20:15:42 PT — `plan-critic: stage=post round=3 result=SEND_BACK collisions=1 open=0 — DRE-6358: the "you have not been asked anything on this card before" form …` — the fourth.

The other three classed lines are the first critic's round 1 of 2026-10-08 19:05:57 PT, and the second critic's two send-backs on the fresh attempt of 2026-10-09 (12:37:11 and 12:55:57 PT). That fresh attempt began with `plan-cycle: start epic=DRE-4059` at 2026-10-09 12:05:08 PT. `--whole-thread` read past it. A search of all 98 comment bodies for `bound-rewrite` found none.

The why each line carries, `goes to the CEO as a decision`, is the classifier's own text for an uncertain finding. On the `post` stage, the exit's stage rule (`UNCERTAIN_BY_STAGE`, DRE-6452) takes the rewrite for it instead, which is what the outcome word and the stderr line say. The row is judged on that decision, as the criterion asks.

## Row 2 — the hygiene lane names the canary with its age

The canary's stamp, read off DRE-6562's comments (`dump-comments DRE-6562 --with-authors`), posted 2026-10-09 21:29:35 PT, `authored_by_pipeline: false`:

    🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml

`HYGIENE_CARD` is DRE-5774, read off run 38060689517's job environment (`HYGIENE_CARD: DRE-5774`), because the read token cannot read repository variables. The summary of the pass that alarmed — 2026-10-10 05:52:25 PT, run 38053473320 (scheduled, 05:50:58 → 05:52:27 PT, `hygiene: event=schedule dry_run=false`) — opens `🧹 hygiene: summary e00c39d42cc4 · 05:52 PT` and carries:

    - left DRE-6562 — held plan-critic-bound for 8 hours in Triage — the card carries no park note — recommend: read the park note's own way back — clear needs-human and move the card to Planning for a fresh planning attempt, or answer the Green Light question when the exit asked one

Every summary on DRE-5774 dated after the stamp names the canary the same way, its hours counting up:

| Summary posted | Opens | Hours in its `left DRE-6562` line |
| -- | -- | -- |
| 2026-10-09 21:50:18 PT (run 38025328206) | `🧹 hygiene: summary 0a852be1d5ae · 21:50 PT` | 0 hours |
| 2026-10-09 22:47:05 PT | `🧹 hygiene: summary 8bb238a177b3 · 22:47 PT` | 1 hour |
| 2026-10-09 23:57:28 PT | `🧹 hygiene: summary 4295b4655a07 · 23:57 PT` | 2 hours |
| 2026-10-10 00:48:05 PT | `🧹 hygiene: summary 4295b4655a07 · 00:48 PT` | 3 hours |
| 2026-10-10 01:49:01 PT | `🧹 hygiene: summary c847ebdaa646 · 01:49 PT` | 4 hours |
| 2026-10-10 02:45:36 PT | `🧹 hygiene: summary 19704d43e79e · 02:45 PT` | 5 hours |
| 2026-10-10 03:44:02 PT | `🧹 hygiene: summary a67885d955d9 · 03:44 PT` | 6 hours |
| 2026-10-10 04:43:47 PT | `🧹 hygiene: summary b73263a64b58 · 04:43 PT` | 7 hours |
| 2026-10-10 05:52:25 PT (run 38053473320) | `🧹 hygiene: summary e00c39d42cc4 · 05:52 PT` | 8 hours |

The two passes after that, 38056726830 and 38060689517, each logged `hygiene: nothing changed — no action ran and the left set is e00c39d42cc4` and posted no summary, which is the summary's own rule (said once when it changes).

## Row 3 — the alarm, once

The one receipt on DRE-6562, posted 2026-10-10 05:52:13 PT (`authored_by_pipeline: true`), written by run 38053473320 (its `Keep dreadnought-foundry` job logged `commented on DRE-6562` at 12:52:13 UTC):

```
🧹 hygiene: hyg-triage-aged — held plan-critic-bound in Triage since 2026-10-10T04:29:35Z past the 8-hour bound · 05:51 PT
evidence: 🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml, stamped 2026-10-10T04:29:35Z

📎 pipeline-act: hygiene-triage-alarm · kind: hold · state: unchanged · next: operator · discharges: nothing · subscriber: hygiene.yml · tag: hyg-triage-aged
```

DRE-6562 carries two comments in all: the stamp and this receipt.

What the card expected the receipt to open with, and what it opens with:

    expected: 🧹 hygiene: hyg-triage-aged — held plan-critic-bound past the 8-hour bound in Triage ·
    printed:  🧹 hygiene: hyg-triage-aged — held plan-critic-bound in Triage since 2026-10-10T04:29:35Z past the 8-hour bound ·

The released code writes the printed form, `scripts/hygiene_triage.py:344` at `2b550bad`:

    cause = f"held {reason} in Triage since {_iso(since)} past the {bound:g}-hour bound"

That line came in with `b8764159` (`fix(DRE-6451): address review findings (attempt 2)`). The module's docstring says the start time was put in the cause so that the alarm is said once per hold and again when a card is parked anew. The card's contract section still states the earlier form. This run does not judge which wording is right, so the row stays `Not met.` as written.

The scheduled passes read, each with `HYGIENE_LIVE: true` and `hygiene: event=schedule dry_run=false` in its `Read the board` job. The eighth hour since the stamp was reached at 2026-10-10 05:29:35 PT:

| Run | Ran (PT) | What it did to DRE-6562 |
| -- | -- | -- |
| 38053473320 | 2026-10-10 05:50:58 → 05:52:27 | posted the receipt above (`commented on DRE-6562`) |
| 38056726830 | 2026-10-10 06:42:02 → 06:43:10 | `suppressed: DRE-6562 hyg-triage-aged — held plan-critic-bound in Triage since 2026-10-10T04:29:35Z past the 8-hour bound` |
| 38060689517 | 2026-10-10 07:42:35 → 07:44:08 | `suppressed: DRE-6562 hyg-triage-aged — held plan-critic-bound in Triage since 2026-10-10T04:29:35Z past the 8-hour bound` |

The eight earlier live passes, 38025328206 (21:48 PT) through 38049358080 (04:42 PT), wrote no receipt on DRE-6562 and logged no line naming it.

## Row 4 — the four checks

From the worktree of `2b550bad409e6823d3efe09a9e7640f0a739f687`, at 2026-10-10 08:09:28 PT:

```
hold.py check exit=0
17 hold writer site(s), 17 row(s), 0 problem(s)

green_light_rows.py check exit=0
  proof-task.yml#Report proof result to Linear (.github/workflows/proof-task.yml:1738): agent-escalation
10 write(s) into Green Light discovered; 10 arrival(s) declared; 0 problem(s)
  not checkable from here: operator

pipeline_act.py check exit=0
54 act(s) checked against the code that emits them, 0 problem(s)

planning_escalation.py check exit=0
21 label(s) and the planner workflow checked for a way past Planning, 0 problem(s)
```

## Row 5 — the duplicate guard moved nothing

The receipt on DRE-4059, posted 2026-10-08 20:22:59 PT (`authored_by_pipeline: true`):

```
🤖 Duplicate dispatch skipped: a planner run for DRE-4059 was already in flight when this dispatch arrived (run 37878064594) — duplicate dispatch. This run planned nothing and moved no lane. Run: https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37878973834
```

DRE-4059's lane history, read off Linear's issue history (`history(first: 100)` with `fromState`, `toState` and `actor`), every lane move:

```
2026-10-08 18:46:29 PT  Intake -> Planning        actor Frederick Conklin
2026-10-08 20:22:22 PT  Planning -> Triage        actor Agent-Bureau
2026-10-09 11:24:22 PT  Triage -> Planning        actor bureau-tools
2026-10-09 13:06:44 PT  Planning -> Green Light   actor Agent-Bureau
2026-10-09 13:08:05 PT  Green Light -> In Progress actor Agent-Bureau
2026-10-09 22:39:58 PT  In Progress -> Done       actor Agent-Bureau
```

The one Triage entry is 20:22:22 PT. The parking run is 37878064594 (Agent Plan, `repository_dispatch`, 2026-10-08 20:10:40 → 20:22:40 PT, head `411534a`). Its log shows the park step running `python3 .bureau-pipeline/scripts/linear_ops.py state "$EPIC" "Triage"` and printing, at that second:

    2026-10-09T03:22:22.3949683Z DRE-4059 → Triage

The 🛑 park note it posted, `🛑 Parked in Triage with needs-human for an operator — …`, is on the thread at 2026-10-08 20:22:22 PT. The duplicate run is 37878973834 (Agent Plan, 2026-10-08 20:22:26 → 20:23:07 PT, same head). It was created four seconds after the move. Its `dedupe_dispatch.py plan-gate` step printed `skip=true` at 20:22:59 PT. Every later step in it reports `skipped` (`OUTCOME_claude: skipped`, `OUTCOME_replan: skipped`, `OUTCOME_posta: skipped` and the rest), and its log carries no lane move.

The tests DRE-6468 added to `tests/test_plan_dispatch_dedupe.py` (pull request #877, merged as `9f86cf4`; both present at `2b550bad`, lines 704 and 937):

- `test_a_refused_plan_dispatch_writes_no_lane_and_says_so_once` — the skip path writes no lane.
- `test_every_lane_writer_in_the_planner_runs_behind_the_skip` — every lane writer in `plan.yml` sits behind the guard's skip.

The pull request also added a third, `test_the_walk_names_a_lane_writer_that_leaks_past_the_skip`, which mutates the workflow seven ways and checks the walk names each leak.

The released commit's own CI run on `main`: Pipeline Tests run 38040174989 (https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38040174989), event `push`, branch `main`, head `2b550bad409e6823d3efe09a9e7640f0a739f687`, created 2026-10-10 02:05:44 PT, concluded `success` on attempt 2 at 02:35:27 PT. From job `scripts unit tests (part 3)` (114181681437, attempt 2):

```
2026-10-10T09:31:33.2023137Z tests/test_plan_dispatch_dedupe.py::test_a_refused_plan_dispatch_writes_no_lane_and_says_so_once[run-in-flight] PASSED [ 66%]
2026-10-10T09:31:33.2099537Z tests/test_plan_dispatch_dedupe.py::test_a_refused_plan_dispatch_writes_no_lane_and_says_so_once[lane-moved-on] PASSED [ 66%]
2026-10-10T09:31:33.4133619Z tests/test_plan_dispatch_dedupe.py::test_every_lane_writer_in_the_planner_runs_behind_the_skip PASSED [ 66%]
…
2026-10-10T09:35:02.8252878Z ============ 4722 passed, 455 subtests passed in 678.72s (0:11:18) =============
```

All seven cases of the third test also read `PASSED` there. Attempt 1 of the same run concluded `failure`. Its part 3 job (114178666194) passed these same three tests at 02:14 PT and failed one unrelated test: `FAILED tests/test_split_ledger.py::test_the_door_is_asked_for_the_window_start - assert (2592001.0 / 86400) <= 30.0`, then `1 failed, 4721 passed`. The run was re-attempted, and attempt 2 is the run's result.
