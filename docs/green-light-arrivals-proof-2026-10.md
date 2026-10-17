# PROOF record — DRE-6673: PROOF: Green Light holds only the CEO's decisions — seven days of arrivals on the live board after release, each with who it was for (epic DRE-6639)

**Status: PARTIAL.**

This run was dispatched 47 minutes after the release went live, so the seven-day window it observes had barely opened. The release that carries all seven bureau-pipeline merges went live at **2026-10-10 18:59:16 PT**, when `stable` moved to `f46cfc9`. The window closes at **2026-10-17 18:59:16 PT**. The two rows that can be read now are met: the release lines and the four checks on the released commit, which each printed zero problems. The rows that need the whole window are written as waiting for its close, as the card says they will be. Each one records what the first 52 minutes showed. One card entered Green Light in that time. It was DRE-6044, an epic plan both critics passed, waiting for the CEO's approval, so it is one of his decisions. It is none of the four false arrivals of 2026-10-10. Every time is Pacific.

| Criterion | Result |
|---|---|
| The record names, for each of the seven bureau-pipeline build cards (DRE-6649, DRE-6650, DRE-6651, DRE-6652, DRE-6653, DRE-6654, DRE-6655), the tag that carries its merge, read with `python3 scripts/proof_release.py check --repo dreadnought-foundry/bureau-pipeline` as the run's read-only GitHub token with the `proof-release:` lines quoted, and for DRE-6672 (agent-bureau, which rides `main` directly) the merge commit and PT merge time read with `gh pr view --repo dreadnought-foundry/agent-bureau --json mergeCommit,mergedAt`; names the PT time the bureau-pipeline release went live as the opening of the window and the PT time seven days later as its close; and every observation below is dated inside that window. | Met. `stable` carries all seven merges. I ran the check as `GH_READ_TOKEN` at 2026-10-10 19:47 PT. It printed seven lines of the form `proof-release: ready — pipeline-channel (the whole repository): stable carries #<pr> (<sha>) for DRE-<n>` and exited 0. The lines are quoted in §1: #964 for DRE-6649, #962 for DRE-6650, #967 for DRE-6651, #963 for DRE-6652, #974 for DRE-6653, #968 for DRE-6654 and #973 for DRE-6655. **DRE-6672:** `gh pr view 3562 --repo dreadnought-foundry/agent-bureau --json mergeCommit,mergedAt`, read as `GH_READ_TOKEN` at 19:48 PT, answered `{"mergeCommit":{"oid":"97d9488b4b8bfe89703be545641f165b7c568f0d"},"mergedAt":"2026-10-10T22:39:26Z"}`, which is merge commit `97d9488` at **2026-10-10 15:39:26 PT**. **Window opens 2026-10-10 18:59:16 PT.** The last build merge was DRE-6653's #974 (`7dfe1ee`) at 18:39:50 PT. `stable` first carried it at that time, when Promote Channel run [38103581615](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38103581615) printed `harness-passed-promoting: promoting stable to f46cfc92eb3adc2a54231f43c88ae1df616d1f49 — harness green, strictly ahead.` and `channel-record: release recorded: deployment 6989650706 (stable@f46cfc9)`. Deployment 6989650706 was created at 2026-10-11T01:59:16Z, and that run's `linear-release:` line names release `stable-f46cfc9`. The move before it, by run 38103129764 at 18:51:32 PT, went to `eaf0e6c`, and `compare/7dfe1ee...eaf0e6c` answers `behind`, so that move did not carry #974. **Window closes 2026-10-17 18:59:16 PT.** Every observation in this record was made between 19:46 and 19:52 PT on 2026-10-10, inside the window. |
| Live fact: as the fleet key, read only, every card that entered Green Light inside the window is listed with its id, the PT time it entered (read off its own history), the arrival kind and writer that put it there (one of the lane contract's declared `arrivals` on the Green Light entrance, matched by the evidence that kind declares, or `undeclared` with the comment that moved it quoted), and who the row was for in one phrase; the Linear query is quoted. A window with no arrivals reads `Met. none arrived —` with the query quoted. | Not observed. waiting for the window's close at 2026-10-17 18:59:16 PT: the full list of cards that entered Green Light over the seven days, each with its kind, writer and who it was for. **What the first 52 minutes showed:** I read Linear at 19:50:30 PT as the fleet key, read only, through `linear_ops.gql_paged` with `issues(first: 100, after: $after, includeArchived: true, filter: { updatedAt: { gte: "2026-10-11T01:59:16Z" } }) { … history(first: 30) { nodes { createdAt fromState { name } toState { name } actor { name } } } }`. It returned 59 cards. One history entry entered Green Light after the window opened: **DRE-6044**, `Planning → Green Light` at **2026-10-10 19:11:46 PT**. Its kind is **`passed-plan`** and its writer is **`plan.yml`** (`plan.yml#Epic → Green Light — both critics passed`). That is matched by the evidence the kind declares, read off its thread with `dump-comments`: `plan-critic: stage=post round=1 result=PASS collisions=0` on its own, beside the newest pre-stage record, `plan-critic: stage=pre round=1 result=PASS collisions=0`, after the `📋 A fresh planning attempt on DRE-6044 starts here.` mark. **Who it was for:** the CEO, to approve an epic plan both critics passed. |
| Live fact: none of the listed arrivals is one of the four kinds of 2026-10-10 — a `🙋 planning-escalation` on a card whose bound findings are all revision-class (checked with `python3 scripts/plan_bound.py read <CARD> --stage one-off` on the released commit), a `review-cap-spent` hold on a proof-record branch whose critic verdict is `REQUEST_CHANGES` at the head, or `APPROVE` bound to the head with the gate's `proof record not proven:` decline standing at that head, a `🙋 planning-escalation` on an epic carrying an open formal `blockedBy` relation, or a `🙋 planning-escalation` on a card carrying `no-code` or `agent:ops` — and the row names each listed arrival with the kind it was checked against. One such arrival is `Not met.` naming the card. | Not observed. waiting for the window's close at 2026-10-17 18:59:16 PT: every arrival of the seven days checked against the four kinds of 2026-10-10. **What the first 52 minutes showed:** the one arrival, DRE-6044, came in as a `passed-plan` and not as a `🙋 planning-escalation` or a `review-cap-spent` hold. Its thread carries no `🙋` comment and no `🔒 hold:` stamp, so it is none of the four kinds. All four kinds start from one of those two writes, so no further check (`plan_bound.py read`, the record branch's head or its `blockedBy` relations) applied to it. |
| One case of each of the four rules firing inside the window, each either `Met. on the live board —` with the card, the receipt or stand-down line quoted with its PT time and the lane the card went to instead, or `Met. by dry run —` saying none arose in the window and quoting `python3 -m pytest tests/test_green_light_false_arrivals_2026_10_10.py -v` green on the released commit with the six passing ids: the one-off bound granting a rewrite or parking in Triage on revision-only findings; the sweep standing down on an approved proof record the gate declined, with the proof run's re-run or `🔬 proof-waiting` hold named; an epic still to be planned with an open blocker sent to Backlog under `⏸️ epic-waits:`, or carried Backlog → Planning by the sweep (the close hook or the `return_waiting_epics` pass) when its blockers became terminal; a switch-reason alarm stamped `one-off` with no model asked and routed OPERATOR. | Not observed. waiting for the window's close at 2026-10-17 18:59:16 PT: for each of the four rules, either a live case inside the seven days or the dry run beside a window in which none arose. A dry run can only say "none arose" once the window is over. **What the first 52 minutes showed:** the dry run is already green on the released commit. `python3 -m pytest tests/test_green_light_false_arrivals_2026_10_10.py -v`, run at `c5295316` from 19:50:13 to 19:50:16 PT, printed `7 passed in 1.84s`. The six card ids passed: `test_the_card_does_not_land_in_green_light[DRE-6042]`, `[DRE-6364]`, `[DRE-6589]`, `[DRE-6590]`, `[DRE-6617]` and `[DRE-6636]`, beside `test_the_directory_holds_the_six_cards` (§3). One near case of the third rule fell just outside the window. The sweep carried DRE-6044 `Backlog → Planning` at 18:56:48 PT with `🧹 Auto-advanced Backlog → Planning: blocker DRE-6042 is Done, and every blocker is now closed.`, the line `reconcile.py` writes. That was 2 minutes 28 seconds before the window opened, so it does not count as a case inside the window. |
| Observed on the released commit in the run's own checkout: `python3 scripts/green_light_rows.py check`, `python3 scripts/hold.py check`, `python3 scripts/planning_escalation.py check` and `python3 scripts/pipeline_act.py check` each printed zero problems, quoted with their PT times. | Met. — the run's checkout is `c5295316a85f2ce9412eabc72c03afa07fd83072`, which is what `stable` pointed to when I read it as `GH_READ_TOKEN` at 19:47 PT (Promote Channel run 38105389225 moved it there at 19:32:26 PT). It carries `f46cfc9` and all seven merges. All four checks exited 0. `green_light_rows.py check` at 19:50:00 PT printed `11 write(s) into Green Light discovered; 11 arrival(s) declared; 0 problem(s)`. `hold.py check` at 19:50:03 PT printed `17 hold writer site(s), 17 row(s), 0 problem(s)`. `planning_escalation.py check` at 19:50:04 PT printed `21 label(s) and the planner workflow checked for a way past Planning, 0 problem(s)`. `pipeline_act.py check` at 19:50:04 PT printed `57 act(s) checked against the code that emits them, 0 problem(s)`. The first also printed `not checkable from here: operator` (§2). |
| Live fact: the six cards of 2026-10-10 are listed with where each stands at the window's close (lane, and Done or Canceled where it is), read as the fleet key, so the record shows the real cards were answered by the people the epic names and not by the CEO. | Not observed. waiting for the window's close at 2026-10-17 18:59:16 PT: the lane of each of the six cards at that time. **Where they stood at 19:50:59 PT**, read as the fleet key with `issues(first: 10, includeArchived: true, filter: { team: { key: { eq: "DRE" } }, number: { in: [6042, 6364, 6589, 6590, 6617, 6636] } })`, each card's newest move read off its own history: **DRE-6590** Done (`In Review → Done` 2026-10-10 11:46:21 PT). **DRE-6617** Done (`In Review → Done` 14:38:25 PT). **DRE-6042** Done (`Green Light → Done` 15:55:01 PT). **DRE-6364** Green Light (`In Review → Green Light` 02:28:24 PT). **DRE-6589** Green Light (`Planning → Green Light` 11:08:01 PT). **DRE-6636** Canceled (`Green Light → Canceled` 10:17:11 PT). Two of the six were still in Green Light, both since before the window opened. |
| The record is merged on `main` at `docs/green-light-arrivals-proof-2026-10.md`, opens with a criterion table whose rows answer each line above, carries a PT time on every observation, and the CEO closes this card after reading it. | Open: this record's merge and the close happen after it is written. Read them off this record's pull request on `agent/DRE-6673-proof-record`, and off this card's thread for the close receipt. |
| The CEO closes this card after reading the record | Open: the CEO's step |

## How this was recorded

- **The run:** `Proof Task` [38106200551](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38106200551), event `repository_dispatch`, created 2026-10-10 19:45:54 PT, dispatch reason `first proof run`.
- **The commit and release observed:** the run's checkout is `c5295316a85f2ce9412eabc72c03afa07fd83072` (Merge pull request #982), which is `stable` as read at 19:47 PT. The release that opened the window is `stable@f46cfc9` (deployment 6989650706, Linear release `stable-f46cfc9`). Every local command in this record ran at `c5295316`.
- **The window:** opens 2026-10-10 18:59:16 PT and closes 2026-10-17 18:59:16 PT. This run started 46 minutes 38 seconds after it opened. The card says what follows. The operator types one `🔬 proof-waiting` hold naming the close, and discharges it after that time. The proof dispatcher then starts the run that amends this record in place.
- **Local run:** `PROOF_LOCAL_STATUS=none`. No criterion on this card is a `Local screen:` row.
- **pytest:** the runner had no `pytest`. I installed the repository's own pins with `python3 -m pip install --user -r requirements-dev.txt` (`pytest==9.1.1`) before the dry run in row 4. Nothing in the checkout changed.

## Identities

- **GitHub, observed — `GH_READ_TOKEN`:** the step summary reads `proof identity: github read — app agent-bureau-bot-2, owner dreadnought-foundry, permissions contents:read actions:read pull-requests:read metadata:read (variables:read not granted to this installation)`. Every GitHub observation above was made as `GH_TOKEN=$GH_READ_TOKEN gh …`: the pull request lists for the eight build cards, the `proof_release.py check`, `gh pr view` for agent-bureau#3562, the `stable` tag, the compares, the Promote Channel runs, their jobs and logs, deployment 6989650706, and this run's own record. One slip: I first read this run's creation time with `gh run view`, which used `GH_TOKEN`. I read it again as `GH_READ_TOKEN` at 19:51:22 PT (`2026-10-11T02:45:54Z repository_dispatch Proof Task`), and that second read is the one this record uses.
- **The refused write, 2026-10-10 19:51:08 PT:** `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-38106200551 -f sha=c5295316a85f2ce9412eabc72c03afa07fd83072` answered `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}`. A read of that ref then answered `404 Not Found`, so nothing was created and nothing needed deleting.
- **GitHub, written — `GH_TOKEN`:** used only to push `agent/DRE-6673-proof-record` and to open this record's pull request, apart from the slip named above.
- **Linear — `LINEAR_API_KEY`, the fleet key:** used for three reads (the cards updated since the window opened, DRE-6044's thread and the six cards of 2026-10-10) and for this card's heartbeats and the `🤖 agent-actor` marker. Nothing was moved or edited, and no other card was commented on. **linear requests: 16 of 40.** Before this record was written there were 8: the `⏳ 1/5` and `⏳ 2/5` heartbeats at 2 each, the window query at 1, a mistaken `dump-comments DRE-6044 --help` that made a read at 1, DRE-6044's thread at 1, and the six-card query at 1. After it came the `⏳ 3/5`, `⏳ 4/5` and `⏳ 5/5` heartbeats and the actor marker, at 2 each.
- **aws: none.** The step summary reads `proof identity: aws — none, PROOF_ROLE_ARN not provided`. No criterion on this card needs it.

## 1. The release lines

Read as `GH_READ_TOKEN`, 2026-10-10 19:47 PT:

    $ python3 scripts/proof_release.py check --repo dreadnought-foundry/bureau-pipeline \
        --merge DRE-6649:964:e303c38dcad7099a835af768362e97a68fb8cd35 \
        --merge DRE-6650:962:55bcd8a8a47130fbb6518f95a9d3ebf23d35d27a \
        --merge DRE-6651:967:7746d6f2c7bfd5c5e63cac6e737633626d344d75 \
        --merge DRE-6652:963:8a08e0ba92bd0f8a133836432158acba68d4a3d3 \
        --merge DRE-6653:974:7dfe1eecfcab36171da616e20f7e353a86cbe205 \
        --merge DRE-6654:968:a46568f05eb0ef693915876dfb053646919b18b0 \
        --merge DRE-6655:973:c9801f8892748c620faeb6a4952a608ce70ff495
    proof-release: ready — pipeline-channel (the whole repository): stable carries #964 (e303c38) for DRE-6649
    proof-release: ready — pipeline-channel (the whole repository): stable carries #962 (55bcd8a) for DRE-6650
    proof-release: ready — pipeline-channel (the whole repository): stable carries #967 (7746d6f) for DRE-6651
    proof-release: ready — pipeline-channel (the whole repository): stable carries #963 (8a08e0b) for DRE-6652
    proof-release: ready — pipeline-channel (the whole repository): stable carries #974 (7dfe1ee) for DRE-6653
    proof-release: ready — pipeline-channel (the whole repository): stable carries #968 (a46568f) for DRE-6654
    proof-release: ready — pipeline-channel (the whole repository): stable carries #973 (c9801f8) for DRE-6655

The merges, read off each pull request as `GH_READ_TOKEN`:

| Card | Pull request | Merge commit | Merged (PT) |
|---|---|---|---|
| DRE-6649 | bureau-pipeline#964 | `e303c38` | 2026-10-10 15:20:42 |
| DRE-6650 | bureau-pipeline#962 | `55bcd8a` | 2026-10-10 15:08:06 |
| DRE-6651 | bureau-pipeline#967 | `7746d6f` | 2026-10-10 17:30:34 |
| DRE-6652 | bureau-pipeline#963 | `8a08e0b` | 2026-10-10 15:13:27 |
| DRE-6653 | bureau-pipeline#974 | `7dfe1ee` | 2026-10-10 18:39:50 |
| DRE-6654 | bureau-pipeline#968 | `a46568f` | 2026-10-10 17:27:22 |
| DRE-6655 | bureau-pipeline#973 | `c9801f8` | 2026-10-10 18:03:59 |
| DRE-6672 | agent-bureau#3562 | `97d9488` | 2026-10-10 15:39:26 |

How `stable` reached `f46cfc9`, read off Promote Channel's runs and logs as `GH_READ_TOKEN`:

| Run | Candidate | Decision line | Channel moved (PT) |
|---|---|---|---|
| 38103129764 | `eaf0e6c` (behind `7dfe1ee`) | `harness-passed-promoting: promoting stable to eaf0e6c542e2154336cef08fafaa38d4af2dd87e — harness green, strictly ahead.` | 18:51:32 |
| 38103453113 | `d7a045d` | `harness-cancelled-by-newer-push: not promoting d7a045d9b18a1469f785f22662e7549df19f493b …` | skipped |
| 38103581615 | `f46cfc9` (ahead of `7dfe1ee` by 13) | `harness-passed-promoting: promoting stable to f46cfc92eb3adc2a54231f43c88ae1df616d1f49 — harness green, strictly ahead. Mirror check: passed — 41 agent-bureau tests at 7b10bb4 green; 0 failing, 0 already red on stable.` | **18:59:16** |

## 2. The four checks, at `c5295316`

    2026-10-10 19:50:00 PT  $ python3 scripts/green_light_rows.py check   → exit 0
      …
      plan.yml#Route — plan or activate (.github/workflows/plan.yml:1032): queued-epic
      plan.yml#Epic → Green Light — both critics passed (.github/workflows/plan.yml:4267): passed-plan
      proof-task.yml#Report proof result to Linear (.github/workflows/proof-task.yml:1760): agent-escalation
    11 write(s) into Green Light discovered; 11 arrival(s) declared; 0 problem(s)
      not checkable from here: operator

    2026-10-10 19:50:03 PT  $ python3 scripts/hold.py check   → exit 0
    17 hold writer site(s), 17 row(s), 0 problem(s)

    2026-10-10 19:50:04 PT  $ python3 scripts/planning_escalation.py check   → exit 0
    21 label(s) and the planner workflow checked for a way past Planning, 0 problem(s)

    2026-10-10 19:50:04 PT  $ python3 scripts/pipeline_act.py check   → exit 0
    57 act(s) checked against the code that emits them, 0 problem(s)

## 3. The dry run, at `c5295316`

    2026-10-10 19:50:13 PT  $ python3 -m pytest tests/test_green_light_false_arrivals_2026_10_10.py -v
    tests/test_green_light_false_arrivals_2026_10_10.py::test_the_directory_holds_the_six_cards PASSED [ 14%]
    tests/test_green_light_false_arrivals_2026_10_10.py::test_the_card_does_not_land_in_green_light[DRE-6042] PASSED [ 28%]
    tests/test_green_light_false_arrivals_2026_10_10.py::test_the_card_does_not_land_in_green_light[DRE-6364] PASSED [ 42%]
    tests/test_green_light_false_arrivals_2026_10_10.py::test_the_card_does_not_land_in_green_light[DRE-6589] PASSED [ 57%]
    tests/test_green_light_false_arrivals_2026_10_10.py::test_the_card_does_not_land_in_green_light[DRE-6590] PASSED [ 71%]
    tests/test_green_light_false_arrivals_2026_10_10.py::test_the_card_does_not_land_in_green_light[DRE-6617] PASSED [ 85%]
    tests/test_green_light_false_arrivals_2026_10_10.py::test_the_card_does_not_land_in_green_light[DRE-6636] PASSED [100%]
    ============================== 7 passed in 1.84s ===============================

## 4. Green Light arrivals so far

Read as the fleet key at 2026-10-10 19:50:30 PT. The window had been open 51 minutes.

| Card | Entered (PT) | From | Kind · writer | Evidence | For |
|---|---|---|---|---|---|
| DRE-6044 | 2026-10-10 19:11:46 | Planning | `passed-plan` · `plan.yml` | `plan-critic: stage=post round=1 result=PASS collisions=0`, beside `plan-critic: stage=pre round=1 result=PASS collisions=0` on the current planning attempt, then `✅ Ready for you in Green Light. Both critics passed this plan…` | the CEO, to approve an epic plan |
