# Shell extraction proof — DRE-5386 (epic DRE-3488)

**Status: PROVEN on observations 1 to 6. Observation 7 is met on its counts, with two differences the card did not expect, both explained below.** Each of the five extracted scripts was seen running in a real fleet run after `stable` advanced onto the ceiling card's merge, and each run posted the receipt, note or verdict its outcome calls for. Every pipeline `run:` block on the promoted commit is under 8,000 characters. The largest is 7,083.

- **The promoted commit** is `13d82ca` (DRE-5385's merge, PR #678). `stable` moved onto it at **2026-10-02 18:20 PT** (Promote Channel run [37085678909](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37085678909)). Harness run [37085142299](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37085142299) proved it first, and its `agent_task_parses` scenario passed.
- **The runs** are in portico, the one product repo that ran builds and fixes after the advance. agent-bureau, agent-bureau-demo and bureau-pipeline had their build and fix workflows paused, and atlas and deltasolv ran none. The build and fix runs rode `863ed6a`, which is `stable` three commits past `13d82ca`. The merge and review runs rode `f7d662f`, today's `stable`.
- **Not changed from the card:** the record is read-only. No run was dispatched, and no card or PR was touched to produce it.

**How this was recorded:** it is read from the live records after the fact, between 12:55 and 14:30 PT on 2026-10-03, by a proof-runner session on the operator's instruction. The sources were the Actions run pages and logs (`gh run view <id> --log`), the PR threads (`gh api`), and card receipts and lane changes read from the console database (`make db-read`, the `event` table). Measurements in §6 and §7 were run on source tarballs of `13d82ca`, `f7d662f` and `db6adf6` fetched from the GitHub API, with `GITHUB_ACTIONS=true`, empty `AWS_*` and `LINEAR_API_KEY=test`.

| Criterion | Result |
|---|---|
| The record is on `main`, opens with a status line, and records all seven observations against live state after the channel advanced, with run URLs, Pacific times and the promoted sha | **Met** once this pull request merges (§1–§7) |
| Observations 2 to 5 each name a real production run whose step log shows the script invocation, and the receipt, note or verdict it posted on the live card or PR | **Met.** Build portico [37090336799](https://github.com/dreadnought-foundry/portico/actions/runs/37090336799), fix [37090313940](https://github.com/dreadnought-foundry/portico/actions/runs/37090313940), merge gate [37149335317](https://github.com/dreadnought-foundry/portico/actions/runs/37149335317), review [37148780585](https://github.com/dreadnought-foundry/portico/actions/runs/37148780585) (§2–§5) |
| Observation 6 shows each `run:` block under 8,000 on the promoted commit, beside the baselines | **Met.** The largest block is 7,083 raw, down from 28,856. The largest interpolated block compiles to 4,038, down from 17,963 (§6) |
| The CEO closes this card after reading the record | Open. Under the CEO's rule of 2026-10-02 a proof closes on evidence, so the proof runner closes it when this record merges |

## 1. The channel advanced onto the ceiling card's merge

- DRE-5385 ("no run block in the pipeline's workflows exceeds 8,000 characters") merged as PR #678, merge commit `13d82ca9a5d56138c50f2e3f74c5a37968df3fa0`, at **18:11:33 PT on 2026-10-02**.
- Harness run [37085142299](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37085142299) ran on `13d82ca` from 18:11:36 to 18:19:56 PT, conclusion `success`. Its log:

```
harness run main-gha-37085142299-1 on dreadnought-foundry/bureau-harness [namespace main]: scenarios ['agent_task_parses', 'bot_pr_flow', 'dependabot_flow', 'gate_paths', 'lane_contract']
[agent_task_parses] dispatch produced run 37085167069 (https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37085167069)
[agent_task_parses] 1 job(s) started on run 37085167069 — agent-task.yml compiled
[agent_task_parses] agent-task.yml parses at 13d82ca9a5d5 — the commit under test
  agent_task_parses: PASS
```

- Promote Channel run [37085678909](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37085678909), started 18:19:58 PT:

```
2026-10-03T01:20:06.6671683Z harness-passed-promoting: promoting stable to 13d82ca9a5d56138c50f2e3f74c5a37968df3fa0 — harness green, strictly ahead.
2026-10-03T01:20:07.3026555Z {"ref":"refs/tags/stable", … "object":{"sha":"13d82ca9a5d56138c50f2e3f74c5a37968df3fa0" …
2026-10-03T01:20:08.0915905Z channel-record: release recorded: deployment 6821545751 (stable@13d82ca)
```

`stable` read `13d82ca` from **18:20:07 PT**. Today it reads `f7d662f`, which `gh api …/compare/13d82ca…f7d662f` reports as `ahead` by 116 and behind by 0.

**Verdict: proven.**

## 2. A build

Portico Agent Task run [37090336799](https://github.com/dreadnought-foundry/portico/actions/runs/37090336799) built card DRE-5680, dispatched by `repository_dispatch` (`agent-execute`). It ran from 19:35:31 to 20:11:32 PT on 2026-10-02, conclusion `success`.

- Pipeline checkout: `HEAD is now at 863ed6a Merge pull request #679 …`. `compare/13d82ca…863ed6a` reports `ahead` by 3, so this run is on the channel after the advance.
- The `Report result to Linear` step, at 20:11:22 PT:

```
##[group]Run bash .bureau-pipeline/scripts/report_agent_result.sh
  CARD: DRE-5680
  CLAUDE_OUTCOME: success
commented on DRE-5680
DRE-5680 → In Review
```

- The card's receipt and lane, read from the console database:

```
DRE-5680  2026-10-03T03:11:23.702Z  comment  🤖 PR opened: https://github.com/dreadnought-foundry/portico/pull/897 — CI + critic review running. Run: https://github.com/dreadnought-foundry/portico/actions/runs/37090336799
DRE-5680  2026-10-03T03:11:24.452Z  state    In Review
```

The receipt landed at 20:11:23 PT and the card moved to In Review, the lane that receipt names, at 20:11:24 PT. (PR #897 later merged, at 20:27:57 PT.)

**Verdict: proven.**

## 3. A fix

Portico Agent Fix run [37090313940](https://github.com/dreadnought-foundry/portico/actions/runs/37090313940), `Agent Fix #883`, dispatched by `workflow_dispatch`. It ran from 19:35:08 to 19:38:07 PT on 2026-10-02, conclusion `success`, with the pipeline at `863ed6a`.

- `Resolve PR, mode, and attempt budget`, at 19:35:26 PT:

```
##[group]Run bash .bureau-pipeline/scripts/resolve_fix_pr.sh
  PR_NUMBER: 883
bureau-card: DRE-5659
fix-budget: mode=fix attempts=0 action=run rearmed=false notice=no nonconverging=0 stopped_by=none
fix budget: 0 of 2 stop-budget spent, attempt 1 of a 6 ceiling on PR #883
```

- The step's outputs were populated. `go` was true: the steps gated on it ran (`Announce fix attempt`, `Fix`, `Report`, all `success`). The `Report` step received `card`, `attempt` and `mode` as its environment:

```
##[group]Run bash .bureau-pipeline/scripts/report_fix_result.sh
  CARD: DRE-5659
  PR: 883
  ATTEMPT: 1
  MODE: fix
dreadnought-foundry/portico#883@ab828905: answering verdict @ab828905
https://github.com/dreadnought-foundry/portico/pull/883#issuecomment-5964716971
commented on DRE-5659
DRE-5659 + label 'needs-human'
DRE-5659 → Triage
```

- The attempt receipt on the PR thread, [issuecomment-5964716971](https://github.com/dreadnought-foundry/portico/pull/883#issuecomment-5964716971), was posted at 19:37:56 PT by `agent-bureau-bot[bot]`. It opens `🛑 Fix attempt 1 blocked: Nothing for the fixer to fix on this PR. The only red check is a CI time limit that main hits too.` and ends with its attribution trailer:

```
📎 pipeline-act: fix-attempt-disputed · kind: hold · state: escalated · next: operator · discharges: nothing · subscriber: reconcile.yml · tag: fix-attempt-blocked

🧷 answers: dreadnought-foundry/portico#883 · card DRE-5659 · verdict ab828905
```

This attempt ended blocked, not fixed. The red check was CI's 22-minute Test step limit, which `main` hit as well. That is a legitimate outcome of the script, and the receipt is the one that outcome calls for. DRE-5680 (§2) is the card that raised that limit. PR #883 merged later, at 22:00:51 PT.

**Verdict: proven.**

## 4. A merge

Portico Merge Gate run [37149335317](https://github.com/dreadnought-foundry/portico/actions/runs/37149335317), started by `workflow_run` at 12:50:18 PT on 2026-10-03, conclusion `success`. Pipeline checkout `HEAD is now at f7d662f …`.

```
2026-10-03T19:50:31.2527646Z ##[group]Run bash .bureau-pipeline/scripts/evaluate_and_merge.sh
2026-10-03T19:50:38.8466058Z note=head is diverged relative to its base — merged as it stands: the fleet does not require up-to-date branches (DRE-2416), and CI on the base branch is what catches a green-alone-red-together interaction
2026-10-03T19:50:45.5424612Z merged PR #905
```

`gh pr view 905 -R dreadnought-foundry/portico` reports `mergedAt=2026-10-03T19:50:43Z by=app/agent-bureau-qa-bot`. PR #905 (`agent/DRE-5539-whats-new-shell`) merged at **12:50:43 PT**, by the QA bot, from the gate's own run.

**Verdict: proven.**

## 5. A review

Portico QA Review run [37148780585](https://github.com/dreadnought-foundry/portico/actions/runs/37148780585), started by `pull_request` on PR #905 at 12:41:10 PT on 2026-10-03, conclusion `success`. Pipeline checkout `HEAD is now at f7d662f …`.

```
2026-10-03T19:45:57.9711220Z ##[group]Run bash "$PIPELINE_DIR/scripts/post_verdict.sh"
2026-10-03T19:45:59.2533924Z https://github.com/dreadnought-foundry/portico/pull/905#issuecomment-5972845780
```

The verdict comment, [issuecomment-5972845780](https://github.com/dreadnought-foundry/portico/pull/905#issuecomment-5972845780), was posted at 12:45:58 PT by `agent-bureau-qa-bot[bot]`:

```
🔎 QA Critic — VERDICT: APPROVE @805ace557c07201555e40737138d2d684ebc395a content:be8f2453feaebce75175be869ede495af1b9d1ddbee4cdb0db6d201b50514ec8
```

`805ace5…` is PR #905's head (`gh pr view 905 --json headRefOid`), so the verdict was posted against the reviewed head. It is also the verdict the merge in §4 acted on.

**Verdict: proven.**

## 6. The sizes

Measured with the arithmetic `tests/test_workflow_expression_budget.py` uses. For every `run:` in `.github/workflows/*.yml`, the raw size is `len(script)`. The compiled size of an interpolated block (one containing `${{`) is `len + count("{") + count("}")`. The baseline at `db6adf6` was re-measured the same way and reproduces the card's figures exactly.

| | `db6adf6` (2026-10-01, baseline) | `13d82ca` (promoted) | `f7d662f` (`stable` today) |
|---|---|---|---|
| largest raw | 28,856 merge-gate / Evaluate and merge | **7,083** linear-sync / Card → Done | 7,083 linear-sync / Card → Done |
| 2nd | 26,127 agent-task / Report result to Linear | 4,934 qa-review / Fail if critic never really ran | 4,934 (same) |
| 3rd | 22,799 agent-fix / Report | 4,300 linear-sync / Dispatch fix agents for newly conflicted PRs | 4,756 qa-review / Resolve PR |
| 4th | 22,418 qa-review / Post verdict or neutral status | 4,161 model-trial / Score the trial | 4,300 linear-sync / Dispatch fix agents… |
| 5th | 17,921 agent-fix / Resolve PR, mode, and attempt budget | 4,018 agent-fix / Escalate checks the loop structurally cannot fix | 4,221 agent-fix / Escalate checks… |
| largest interpolated, compiled | 17,963 agent-fix / Resolve PR… | **4,038** agent-fix / Escalate checks… | 4,772 qa-review / Resolve PR |
| blocks over 8,000 | 5 | **0** | 0 |
| `run:` blocks counted | 324 | 338 | 345 |

All five baseline giants are now one-line script calls, and nothing on the promoted commit or since is over the ceiling.

**Verdict: proven.**

## 7. The checkers

Run on `13d82ca`, and on `db6adf6` under the same conditions for comparison:

| Command | `db6adf6` | `13d82ca` |
|---|---|---|
| `python3 scripts/check_act_receipts.py check` | 199 receipt sites, 0 problems | **218 receipt sites, 0 problems** (34 composed, 184 declared unconverted), exit 0 |
| `python3 scripts/pipeline_act.py check` | 31 acts, 0 problems | **43 acts, 0 problems**, exit 0 |
| `python3 scripts/ready_lane_writers.py check` | 85 writes into 11 lanes | **128 writes into 10 lanes**, 0 problems with the default lane supplied (see below) |
| `python3 scripts/lane_callers.py callers scripts/code_owner_hold.py park` | `.github/workflows/merge-gate.yml#Evaluate and merge` / `scripts/code_owner_hold.py#_cmd_hold` | the same two lines, exit 0 |

The park probe still names `merge-gate.yml#Evaluate and merge`. The lane write moved into `scripts/evaluate_and_merge.sh`, and the checker still attributes it to the step that calls it, so lane accounting survived the move.

**Two differences from what the card expected:**

1. **`config/pipeline-acts.json` is not unchanged since `db6adf6`.** Twelve `hygiene-*` acts were added (DRE-5368, `2b70f50`, 2026-10-02), and `card-stranded` was edited. That accounts for 31 → 43 acts and part of the rise in receipt sites and lane writes. None of those commits is one of the twelve move cards. Every checker still reports 0 problems, so the extraction lost no receipt and no act.
2. **`ready_lane_writers.py check` cannot read the team's default lane offline.** With credentials blanked it prints `[FAIL] linear:team-default: … no workspace declaration in reach and no answer from Linear` and exits 1, on `db6adf6` as on `13d82ca`. That holds even with `LINEAR_WORKSPACE_CONFIG` pointed at agent-bureau's `config/linear-workspace.json`. The checker reads a top-level `defaultIssueState`. The declaration has carried it nested, as `"team": {"key": "DRE", "defaultIssueState": "Planning"}`, since DRE-2751, so the offline read never finds it. With the same value given at the top level (`{"defaultIssueState": "Planning"}`), the check reports `128 write(s) into 10 lane(s) discovered; ready work is Backlog, Todo; 0 problem(s)`. This is a pre-existing reader/declaration mismatch, not something the extraction caused. It is reported for a card of its own.

**Verdict: proven on the counts, with the two differences above.**

## What is not proven, and why

- **Nothing else in the seven observations.** Observations 2 to 5 rest on one run each, as the card asks. The fix run ended in a blocked attempt, not a pushed fix, so the push path of `report_fix_result.sh` is not what was observed here. Its receipt path, labeling and lane move were observed.
- **The runs are portico's.** In agent-bureau, bureau-pipeline and agent-bureau-demo, builds and fixes were paused from 2026-10-02 15:17 PT. That is why the build and fix are portico runs, and why no run from those three repos is quoted for §2 or §3.

## Where each source disagreed, and which was taken

| Fact | Source A | Source B | Taken |
|---|---|---|---|
| What stopped fix attempt 1 on PR #883 | PR comment: "Nothing for the fixer to fix … The only red check is a CI time limit" | DRE-5659's Linear receipt: "The fix agent disagrees with the reviewer's blocking finding" | The PR comment. The critic had APPROVED `ab82890`, so there was no blocking finding to disagree with. The Linear wording is a fixed template that does not fit this outcome, and it is reported separately |
