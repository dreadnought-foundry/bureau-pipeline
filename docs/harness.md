# The integration harness — what it covers

`bureau-harness` is the sandbox that proves a bureau-pipeline commit before
`promote-channel.yml` advances `stable` onto it, and before `release-gate.yml`
lets a `v*` tag stand. The driver lives in `scripts/harness/` (its README is
the implementation guide); this page answers the one question that README
never did: **which workflows does the sandbox actually cover?**

It exists because the answer used to be nowhere. On 2026-09-09
`agent-task.yml` — the one workflow that builds every card in the fleet —
became unparseable to GitHub (DRE-3484: a `run:` block carrying `${{ }}`
compiles into one expression, and that step's script had reached ~23,260
characters against a hard 21,000 ceiling). The harness ran on that exact
commit at 22:21 PT, passed, the channel advanced onto it, and from 06:15 PT
every `agent-execute` dispatch in every repo on the channel was refused before
a job started. Three hours and twenty minutes with no build lane.

The harness did nothing wrong. It reported on what it covered, and
`agent-task.yml` was not in it — **and nothing anywhere stated the intended
set, so the absence was invisible.** This list is that statement. Read it
beside `.github/workflows/` in this repo and beside
`bureau-harness/.github/workflows/`; a workflow in either that is not on this
list is either a gap or an entry somebody forgot to write down.

## The sandbox's stubs

Every stub is installed **by hand** in `bureau-harness` — that repo is not in
the fleet's roster and nothing in the pipeline dispatches at it. Each one is
pinned `@main` on purpose: bureau-harness is what PROVES a commit, so it is
never pinned to the channel it is proving.

| stub in `bureau-harness/.github/workflows/` | what it is | which rehearsal fires it |
| --- | --- | --- |
| `agent-task.yml` | calls this repo's `agent-task.yml` — the workflow that builds every card | `agent_task_parses` |
| `qa-review.yml` | calls this repo's `qa-review.yml` — the adversarial critic | `bot_pr_flow`, `dependabot_flow`, `gate_paths` |
| `merge-gate.yml` | calls this repo's `merge-gate.yml` — CI green + APPROVE ⇒ qa-bot merges | `bot_pr_flow`, `gate_paths` |
| `reconcile.yml` | calls this repo's `reconcile.yml` — the ~15-minute sweep, and the `workflow_dispatch` review route | `dependabot_flow` |
| `proof-task.yml` | calls this repo's `proof-task.yml` — the proof run on a PROOF card (DRE-5924). Installed under DRE-6034 (bureau-harness PR #3551) | `proof_task_parses` |
| `ci.yml` | the sandbox's OWN product CI — no counterpart here. The merge gate fail-closes to `wait` when a head carries no non-review checks, so the sandbox needs at least one workflow reporting a check run | `bot_pr_flow`, `gate_paths` (indirectly) |

`linear-sync.yml` is deliberately absent: harness branches carry no `DRE-n`
reference, so every Linear touchpoint in the pipeline no-ops on them by
design, and the harness writes nothing to Linear at all (`scripts/harness/
README.md`, "The Linear side").

## The rehearsals

Discovered by convention — one module per scenario in
`scripts/harness/scenarios/`, each exporting `SCENARIO`. The **default sweep**
(an empty `scenarios` input) runs every rehearsal that does not spend a real
build-agent run; that sweep is what every push to `main` runs, and therefore
what the channel gate reads. (It ran on boundary pull requests too until
DRE-4149; the harness now proves `main` once — see `docs/self-hosting.md`,
"The harness proves main, not every pull request".)

| rehearsal | in the default sweep | what it proves |
| --- | --- | --- |
| `agent_task_parses` | yes | a real `agent-execute` `repository_dispatch` produces a run with `jobs > 0`, whose `referenced_workflows` names this repo's `agent-task.yml` with a resolved commit |
| `bot_pr_flow` | yes | the happy path: worker-bot authorship → sha-bound critic verdict → qa-bot merge (author ≠ merger) |
| `dependabot_flow` | yes | the real Dependabot PR self-skips clean, the reconcile dispatch route produces a bound verdict, and the receipt lifecycle holds |
| `gate_paths` | yes | merge-gate semantics: a behind-base merge at the reviewed head, condition D's waiting-for-human state, and a stale-verdict race |
| `lane_contract` | yes | `config/lane-contract.json`'s conformance rules over the live board, the console's state lists and this checkout's own vocabulary |
| `proof_task_parses` | yes | a real `proof-execute` `repository_dispatch` produces a run with `jobs > 0`, whose `referenced_workflows` names this repo's `proof-task.yml` with a resolved commit — `agent_task_parses`, copied, for the proof run (DRE-6045) |
| `noop_resubmission`, `partial_delivery`, `checklist_gaming`, `already_live`, `unverified_claim` | no — opt-in by name | the DRE-2490 adversarial corpus: a real build agent, on the shipped prompt, against a replayed rejection |

## `agent_task_parses`, and why it must FIRE rather than install (DRE-3486)

GitHub validates a **called** workflow only at dispatch. A file can be valid
YAML, pass every linter and pass every parse test in this repo, and still be
refused at the moment it is used — which is exactly what happened on
2026-09-09. So the rehearsal sends the same `agent-execute`
`repository_dispatch` the bureau-linear-relay sends, at a seeded sandbox card,
and reads what GitHub did with it:

* **`jobs > 0` on the run.** A workflow GitHub refuses to compile concludes
  having started nothing. A completed run with `total_count: 0` jobs — and
  `{"billable": {}}` timing beside it — is that refusal and nothing else, so
  the rehearsal fails at once with *"agent-task.yml was refused before any job
  started — the workflow does not parse at this commit"*, inside one sandbox
  wait budget rather than at the 70-minute verdict deadline.
* **`referenced_workflows` names this repo's `agent-task.yml`**, with a
  resolved commit. A started job on somebody else's workflow proves nothing
  about ours; this is the caller/callee contract.

It never waits for the agent to build anything — a started job is the whole
claim — and it cancels the run it started, so a rehearsal costs a job start
rather than a build.

**What it does not claim.** The commit the sandbox compiled is recorded beside
the commit under test (`HARNESS_TESTED_SHA`), and a difference is reported,
not failed. The sandbox's stubs ride `@main` and GitHub resolves that ref at
dispatch, while `harness.yml` queues runs rather than cancelling them
(`cancel-in-progress: false`, DRE-3070) — so a branch proved by hand, which
is not on `main` at all, and a push overtaken by a merge burst are both
compiled as whatever `@main` was at dispatch time. Failing on that difference
would turn every by-hand run and every merge-burst push red on the pipeline's
own design. (It is also why the pull-request run was dropped, DRE-4149: it
compiled `main`'s workflows, not the pull request's.)

**A missing stub says so.** Until the operator has installed
`agent-task.yml` in `bureau-harness`, the rehearsal reports *a missing stub*
rather than a workflow that fails to parse.

## `proof_task_parses` — the same rehearsal, for the proof run (DRE-6045)

`scripts/harness/scenarios/proof_task_parses.py` is a **copy** of
`agent_task_parses.py` with its constants changed — nothing lifted, subclassed
or shared, because a refactor of that module is what ran DRE-3101 into its
turn cap. It fires `proof-execute` with the sweep's own proof payload
(`plan_run.payload(card, reason=…)`, as `proof_dispatch.py` sends it) at the
sandbox's `proof-task.yml` stub, and asserts everything above against this
repo's `proof-task.yml`. A 404 on the stub is reported as *"bureau-harness has
no proof-task.yml stub"*, naming DRE-6034, the card that installed it. A fix
to one of the two modules is probably owed to the other.

## The builder's model line on the probe pull requests (DRE-7619)

A real build posts `🧠 model-attempt: <model-id> — <role> agent starting`, and
the critic reads the build's model off it to review on a model the build did
not run on. No agent builds a probe, so by default the sandbox critic reviews
every probe as built on an unknown model. With the switch `on`,
`bot_pr_flow` and every leg of `gate_paths` end each probe description with
one more line, after a blank one, `What's new: none` staying the first:

    🧠 model-attempt: claude-opus-5-5 — harness agent starting (a fixture: no agent built this probe; it names the model a real build runs on)

The model is the `workhorse` ladder's first rung as `scripts/model_fallback.py`
reads it, never typed (`scripts/harness/builder_line.py`). The sandbox critic
then reads the probe as an Opus-built change and reviews it on the model real
repos' known-builder reviews run on. With the switch `off`, every probe
description is byte for byte what it was before.

**The switch.** `harness.yml` resolves it once per run, in this order:

1. the `builder_line` dispatch input (`on` or `off`; empty means "use the
   variable"), which only a by-hand run carries;
2. the bureau-pipeline repository variable `HARNESS_BUILDER_LINE`, which
   every push run follows;
3. `off`.

Any other value counts as `off` and prints one `::warning::`. Every run logs
`harness-builder-line: <on|off> (from <input|variable|default>)` once, before
any scenario starts. The dispatch input is for the measurement runs: run them
on a throwaway branch, never on `main`, while every push run keeps proving
`main` on the variable. Setting `HARNESS_BUILDER_LINE` to `off` is the
one-step rollback. The next harness run that starts reads it when its job
starts, with no pull request and no review wait, and a run already going
keeps the value it started with (`docs/model-choice-levers.md`, the
`DRE-7187` row).

**The known limit.** `dependabot_flow` reviews a pull request Dependabot itself
opens, so no fixture writes its description and the switch cannot reach it.
That review stays on the unknown-builder walk, on Opus 5.5 — about one probe
review in five.

## Adding a workflow to the fleet

A new reusable workflow here is not covered until a stub exists in
`bureau-harness` and a rehearsal fires it. Both halves, and this page updated
in the same pull request — the gap that cost 2026-09-09 was not that anyone
decided to skip `agent-task.yml`, it was that nobody could see it had been
skipped.
