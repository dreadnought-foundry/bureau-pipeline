# bureau-pipeline

The Agent Bureau's pipeline, defined once. Every product repo
(EveryBite/atlas, dreadnought-foundry/deltasolv, …) runs the SAME eight
workflows from here via GitHub reusable workflows.
A change merged to `main` in this repo is live on the next trigger in every
repo pinned there — `@main` is the rolling channel, and only the canary
repos ride it. **The fleet pins `@stable`**, a moving tag this repo advances
by itself onto the newest `main` commit the integration harness has proved,
paired with `pipeline_ref: stable`. Hand-cut `vN` tags still exist and stay
the operator's to cut and move. See "Release channel" below.

```
Linear card → relay Lambda → repository_dispatch on the product repo
  → thin stub workflow in the product repo
  → reusable workflow HERE (agent-task, qa-review, merge-gate, agent-fix,
    medic, reconcile, plan, linear-sync)
```

CI is deliberately NOT here — `ci.yml` stays product-specific in each repo.

## The lane contract (DRE-2726)

`config/lane-contract.json` declares every lane's entrance condition, exit
condition, permitted writers and the evidence that justifies occupancy — one
file, read by everything. The guard (`scripts/lane_scope.py`) takes its flow
from it, the sweep takes its stall windows from it, `docs/lane-contract.md` is
rendered from it, and the integration harness asserts the live Linear board
against it on every trunk commit: a state the contract does not name, or a lane
it names that does not exist, fails the harness.

Every clause carries `enforced_from: <phase>`. The harness asserts only clauses
whose phase has shipped, reports the rest as promises, and **fails when a
clause's phase has passed with nothing enforcing it** — so the file is a
schedule that checks itself, not a description that drifts.

    python3 scripts/lane_contract.py check --live   # the live board
    python3 scripts/lane_contract.py render         # rewrite the document

## Build by default; escalate by exception (DRE-1655)

The engineer agent (`agent-task.yml` + `briefs/engineer.md`) is **autonomous by
default**: it researches a card and, when confident, builds and ships it through
the normal PR → critic → merge gates with no human in the loop. It **stops and
asks only by exception** — on genuine uncertainty (ambiguous intent, a
risky/destructive change, or a business A-vs-B decision). When it stops it posts
a **plain-English question** as a Linear comment and parks the card in the
`Green Light` lane — the CEO's "needs you" queue, the same lane epics wait in
for plan approval (DRE-2776; an escalated card is waiting on a judgement, not
broken, so it does not belong in the broken-card lane); the CEO answers and
moves it back to `Todo` to proceed or to `Backlog` to drop it.
The critic + tests remain the correctness backstop on every merge.

There is **no propose-first hard stop**. An earlier design (a read-only
`propose.yml` pass, a `proposed` marker, and relay propose-vs-execute routing)
was built, then **shelved and retired** — it stalled overnight automation and
asked the CEO to approve technical approaches he can't evaluate. The
`propose.yml` workflow and its marker are gone. The relay routing WAS
deployed, though, and ran for about six weeks: it dispatched every ordinary
standalone card as `agent-propose`, an event type no workflow in the fleet
consumed, so those cards were logged as handled and silently never built
(DRE-1980; removed by agent-bureau PR #2008). The `fast-track` label was the
hand-workaround for that bug — the way to route a card past the dead gate —
not a convention: standalone cards do not need it, and nobody should re-add
it. Only the human "needs you" queue (now `Green Light`) and the console
surfacing of it are reused from the propose design.

## Break glass (DRE-2737)

`fast-track` is also the argument for designing one sanctioned exception. An
unenforceable rule with no sanctioned exception grows an unsanctioned one: the
choice is not between having an escape hatch and not having one, it is between
one we designed and one we discover later.

**The marker is `break-glass`**, applied by hand, in Linear, by the operator.
It satisfies the entrance condition for `Todo` — the Todo-entry gate lets the
card through instead of returning it. Three properties make it a designed
exception rather than a hole (`scripts/break_glass.py` owns all three):

1. **Recorded, not undone.** A rule that fights an emergency loses the
   emergency and keeps the rule. The bounce is suppressed and the event is
   written to the card: a notice naming what was skipped, by whom, when, and
   what the card still owes, plus the `break-glass:used` receipt label.
2. **Repaid.** When the work merges, the card returns to `Planning` for the
   classification it skipped instead of going Done — both the `linear-sync`
   fast path and reconcile's merged-PR backstop. The decision reads the
   RECEIPT, never the live marker, so removing the marker mid-flight neither
   strands the card nor erases the debt.
3. **Counted.** `python3 scripts/break_glass.py count` and every full
   reconcile sweep print the number. Read it as a measure of the front door:
   frequent use is not people cheating, it means the front door is too slow,
   and that is the finding.

No expiry, no auto-revoke, no approval step — each is a way for the emergency
path to fail during an emergency. The control is that it is loud and counted,
not that it is hard to use.

**No agent may apply it.** The relay, reconcile, the planner and every agent
share one `LINEAR_API_KEY` and resolve to the operator's own Linear user, so
actor identity cannot tell agent from operator. Enforcement lives at the write
seam instead: `linear_ops.add_label` and the planner's child-label path refuse
the marker outright, and a marker applied by a bot actor (an integration we do
not own) is not honored — the card bounces as if it were absent.

## Releasing a held PR: one recovery, not two (DRE-2813)

A PR the fix loop has held for a human decision is released by **answering on
the PR** — a comment whose first line starts with the words `Operator
decision`. The reconcile sweep reads it within about fifteen minutes and
dispatches the fix loop. That is the whole procedure, and
[docs/held-pr-recovery.md](docs/held-pr-recovery.md) is the operator page for
it.

`gh workflow run agent-fix.yml -f pr_number=<n>` is **not a second,
independent recovery.** With the attempt budget spent it has no attempt to
add — and until DRE-2813 it made things strictly worse: the run fixed nothing,
concluded `success`, and posted a fresh 🛑 hold as the worker bot. The sweep
arms only when no worker-bot comment is newer than the answer (which is what
stops one answer re-dispatching forever), so that hold stood the sweep down
permanently. PR #199, 2026-08-29: answer 15:27:14, hand dispatch 15:29:35, its
hold 15:29:46, sweep stands down 15:33:55, PR never moves.

Now such a dispatch does nothing, posts no hold, and posts one
`dispatch-no-work` notice — the single worker-bot comment the arming rule
ignores — saying the answer is already standing. The sweep's own dispatch
(machine-initiated, `github-actions`) re-arms exactly one attempt, so an
answer buys work instead of a repeat hold. `scripts/fix_budget.py` makes that
decision and reads the answer through the same `fix_context` predicates the
sweep reads, so the two cannot disagree.

## How a product repo consumes this

Each pipeline workflow in the product repo is a thin stub: it owns the
trigger (`on:`), the workflow-level `concurrency:` group, and `permissions:`
(all of which need the trigger context), and delegates everything else:

```yaml
# .github/workflows/agent-task.yml in the product repo
name: Agent Task            # EXACT name — other stubs' workflow_run filters match on it
on:
  repository_dispatch:
    types: [agent-execute]
concurrency:
  group: agent-${{ github.event.client_payload.identifier }}
  cancel-in-progress: false
permissions:
  contents: write
  pull-requests: write
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/agent-task.yml@main
    secrets: inherit
```

Inside a called workflow, `github.event`, `github.event_name`, and
`github.repository` are the CALLER's, so all payload references and job-level
`if:` filters live here and keep working. `vars.CLAUDE_AUTH_MODE` also
resolves from the caller repo (set to `subscription` for OAuth-token auth;
unset/anything else means API-key auth).

Division of labor:

| in the product-repo stub | in the reusable workflow here |
|---|---|
| `name:` (exact — `workflow_run` filters match stub names) | job logic, steps, agent prompts |
| `on:` triggers (incl. product-specific `workflow_run` lists) | job-level `if:` event filters |
| workflow-level `concurrency:` | job-level `concurrency:` (merge-gate, reconcile) |
| `permissions:` (constrains `GITHUB_TOKEN`) | — (jobs inherit the caller's token scope) |
| `secrets: inherit` + `with:` inputs (`pipeline_ref` everywhere; `max_wip` on all three promotion paths; `intake_hold` / `intake_max_age_minutes` / `intake_escalation_cap` on the reconcile and groomer stubs) | `secrets:`/`inputs:` declarations |

### The agent-fix stub's concurrency group (DRE-2810)

Because the group lives in the stub, this one is not fixable from here — every
`agent-fix` stub in the fleet must carry it:

```yaml
concurrency:
  group: agent-fix-${{ github.event.issue.number || inputs.pr_number }}-${{ (github.event_name != 'issue_comment' || github.event.comment.user.login == 'agent-bureau-qa-bot[bot]') && 'work' || github.event.comment.user.login }}
  cancel-in-progress: false
```

Two lanes per PR: everything that can **do work** in one, every no-op trigger
in its own.

GitHub keeps at most **one pending run per group**, so a newly queued run
cancels the previously pending one — and the fix agent's own `🔧 Fix attempt N
pushed` comment is an `issue_comment` on the same PR, which queues an Agent Fix
run. That run skips at the job gate (wrong author, no verdict), but it has
already claimed the slot. On PR #199, 2026-08-29, it evicted the pending
REQUEST_CHANGES trigger one second after it was queued: the PR held a standing
verdict for 24 minutes with no agent working it, `success` / `cancelled` /
`skipped` across the three runs, and nothing red for the medic to find.

A qa-bot verdict and every `workflow_dispatch` share the `work` lane on
purpose. merge-gate routes a merge conflict here by dispatch *before* it looks
at the verdict, so a conflicted PR that draws REQUEST_CHANGES fires both
triggers at once; in one lane they serialize as they always have, and in two
they would put two fix agents on one branch. Two verdicts in a row also share
it and serialize behind `cancel-in-progress: false`. The qa login is a
hardcoded site like the two job-ifs — see the rename procedure in
`tests/test_qa_login_literal_roster.py`.

Audit a repo's stub — no credentials, no network:

```bash
python3 .bureau-pipeline/scripts/fix_concurrency.py audit   # .github/workflows
```

The reconcile sweep runs the same audit in every repo it sweeps and prints a
`fix-concurrency:` line, so drift is checked rather than remembered. Beside it,
`evicted-fix-run:` names any Agent Fix run GitHub cancelled before it started a
job whose trigger was a qa-bot verdict — the case that otherwise reads as the
harmless duplicate-dispatch cancel it shares a conclusion with.

What the product repo still carries:

- `.github/workflows/ci.yml` (+ any other product CI) — product-specific.
  Its *plumbing* is not: see **Shared CI plumbing** below.
- `.github/bureau/overrides.md` — stack, local check commands, migration
  tooling. The engineer/fix/planner agents are instructed to read it.
- `.github/bureau/setup.sh` — OPTIONAL. Run by agent-task/agent-fix before
  the agent starts, with `BOT_TOKEN` (bureau App installation token) in the
  env. Use it for private submodules, toolchain installs, test databases
  (reusable workflows cannot receive `services:` from the caller — start
  containers with `docker run` here instead). Export env for later steps via
  `$GITHUB_ENV` / `$GITHUB_PATH`.

Repo secrets stay per-repo (this repo is PUBLIC — **no secrets, keys, or
tokens may EVER live here, in code or in workflow files**): set
`ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN` + `CLAUDE_AUTH_MODE=subscription`),
`LINEAR_API_KEY`, `BUREAU_APP_ID`, `BUREAU_APP_PRIVATE_KEY`,
`BUREAU_QA_APP_ID`, `BUREAU_QA_APP_PRIVATE_KEY` in each product repo, and
install both bureau GitHub Apps on its org.

## Every workflow that can go red on main has a watcher (DRE-2820)

`self-red-main-repair.yml` watched exactly one workflow by name — `Pipeline
Tests`. DRE-2726 shipped the lane-contract harness as its OWN workflow
(`harness.yml` / "Integration Harness"), nobody added it to that list, and on
2026-08-29 the harness sat red on `main` for fourteen hours while every
Red-Main Repair run concluded `skipped` — including the one three minutes
after the failure. Two approved PRs inherited the breakage, one fix agent
spent attempts on a check that was never its fault, and the cause (two stale
entries in `config/lane-contract.json`, named in plain words by the harness log
the whole time) was found by accident.

The list is now **derived, never remembered**. `scripts/check_workflow_watchers.py`
(a Pipeline Tests step) enumerates the workflow files and enforces two rules:

| population | rule | why |
|---|---|---|
| a `push` trigger that reaches the default branch | must be watched by the **Red-Main Repair** caller | its failure means the branch itself is red |
| can RUN on the default branch at all (`schedule`, `repository_dispatch`, `workflow_dispatch`, `workflow_run`, `issue_comment`) | must be watched by **some** `workflow_run` watcher | usually a crashed run — the medic's job — but "nobody" is never the answer |

One declared exemption, with its reason in the code: nothing watches the medic,
because a medic that watched itself rebuilds the 2026-06-28 crash-loop. Adding
a workflow with no watcher fails CI instead of adding a silent failure surface.

**And a PR now says when a red check is not its fault.** The other half of that
day was that two fix agents and a human could not tell an inherited failure
from a caused one. Before it spends an attempt, `agent-fix` compares the PR's
failing checks against the same checks on the **merge base**
(`scripts/inherited_failures.py`); anything red on both sides was red before
the branch existed, and the loop says so in a comment on the PR and in the
fixing agent's own context. It never holds the PR — it is information, not a
gate — and a base it cannot read reports *unevaluated*, never a pass.

## One WIP cap per repo (DRE-2529)

A repo has ONE work-in-progress cap, and **three** workflows promote Backlog
children through the dependency gate at it:

| workflow | when it promotes |
|---|---|
| `reconcile.yml` | the `*/15` cron sweep (GitHub delivers it 78–100 min apart in practice) |
| `plan.yml` | the moment an epic is activated |
| `linear-sync.yml` | the moment a merge marks a card Done — **and only when that merge actually unblocked a card** (DRE-2930) |

The last two are the anti-stall fast paths: they exist purely to promote work
*now* instead of up to ~80 minutes later. They used to hardcode `MAX_WIP=8`
while `reconcile.yml` took the caller's value — so on a repo capped at 12,
between 8 and 11 cards in flight both fast paths refused and the cron sweep
promoted the same card an hour later at 12. The optimisation switched itself
off exactly inside the band it was built for, and nothing reported it.

The merge path is also **gated** (DRE-2930), because it is the one whose cost
scales with how hard we ship: it used to run a promotion sweep *and* an
epic-close sweep on every merge, so 47 merges in a day bought ~94 extra
full-board passes and helped exhaust Linear's 2,500/hour quota.
`scripts/merge_sweep_gate.py` reads the merged card once and runs a sweep only
when that merge cleared a blocker (promotion) or finished the card's own parent
epic (epic-close). The `*/15` cron is unchanged and remains the backstop for
everything the gate declines.

All three now take a `max_wip` input. To override the cap, pass it on **all
three stubs or none** — a half-repointed stub set gives the repo two caps
again, which is the same defect one level down:

```yaml
# .github/workflows/reconcile.yml, plan.yml AND linear-sync.yml in the product repo
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@stable
    with:
      pipeline_ref: stable   # required, and must match the uses: ref
      max_wip: "12"          # quoted — an unquoted 12 is a YAML int and GitHub
                             # rejects it against a `type: string` input
    secrets: inherit
```

A stub that passes nothing inherits the default, which is correct on its own:
the workflows' declared default is single-sourced from
`reconcile.DEFAULT_MAX_WIP`, the value the script itself uses when `MAX_WIP`
is unset or empty. `scripts/check_wip_cap.py` (a Pipeline Tests step) fails
the build if any workflow re-hardcodes a cap, if a promotion path stops taking
the input, if the declared default drifts from the script's, or if this repo's
own stubs disagree with each other.

## The sweep reads every row (DRE-2681)

Linear serves at most 100 nodes per page and says another page exists only in
`pageInfo`. `backlog_children()` and `active_cards()` asked for
`issues(first: 100)` and selected no `pageInfo`, so the promoter's whole world
was the first 100 rows Linear happened to return. On the 2026-08-26 census the
Backlog held **226** cards: 126 were not promotion candidates — not by policy,
not reported anywhere, and *which* 126 was decided by Linear's default
ordering. The sweep log printed what it considered and never what it never saw.

Both queries now follow `pageInfo.hasNextPage` / `endCursor` to exhaustion
through `linear_ops.gql_paged`, which **refuses** a query that declares no
`$after` — the failure this fixed was silent, and it must not come back
quietly. The pagination test uses a 150-card fixture whose 150th card is the
one that must be found; a 100-card fixture passes against the broken code and
proves nothing.

`scripts/structural_repair.py` is the operator-run repair pass over that full
census. It is deliberately narrow — it inherits a missing `initiative:<x>`
label from a card's parent and nothing else:

```bash
python3 scripts/structural_repair.py report   # read-only (default)
python3 scripts/structural_repair.py repair   # applies the planned labels
```

It resolves **parents before children**, so a parent repaired in the same run
supplies the value to its own children; a top-to-bottom pass reports those
children as unrepairable instead. What it cannot repair it reports, keeping the
two cases apart — "the parent carries no label" (label the parent) and "the
parent is Done or Canceled" (it cannot be fixed first, so this card needs the
label set directly). Nothing needing judgment is repaired: an unknown
`repo:<slug>` is reported, never rewritten.

Every run ends with its own proof line — whether it repaired a card **beyond
row 100**. It counts what LANDED, not what it planned, so a `report` run and a
run that stayed inside the first page both record that they proved nothing, in
those words.

The `initiative:<x>` label does **not** gate promotion. `reconcile.py` never
reads it; what breaks without it is `validate_card.infer_repo` step 2a and the
create seam, which refuses a child that lacks it.

## The plan artifact (DRE-2720)

An epic's CEO-facing output is a published document, not a Linear comment.
`plan.yml` asks the planner for one markdown file — business case, KPIs,
risk assessment, outcome, visual model, the cards, proof and demo — then
**checks it, renders `plan.html`, and publishes it from a separate job**.

The split is deliberate. The planner writes markdown with the `Write` tool it
already had and gains no capability; the artifact is an output of the run. And
because the publish job holds no agent, the document portal never enters the
planner's workspace (`agents.yaml` records the planner's `repoScope`, and
`tests/test_agents_registry.py` checks it against the job the agent runs in).

The contract is `standards/plan-artifact.md`; the checker is
`scripts/plan_artifact.py`, which the run uses as a GATE — an artifact missing
a section, carrying prose where the ```kpis block belongs, or offering a
screenshot where a UI epic needs a live mockup fails the run, and the epic
stays in Planning rather than reaching the CEO incomplete.

Two repo variables configure publishing; both are optional:

| variable | effect |
|---|---|
| `PLAN_PORTAL_REPO` | the document portal repo (e.g. `dreadnought-foundry/portico`). Unset ⇒ no publish; the artifact is uploaded as a build output and the epic comment says so rather than inventing a URL. |
| `PLAN_PORTAL_BASE_URL` | the portal's public base. Unset ⇒ the run publishes but posts the run link, because a URL the CEO follows to a 404 is worse than none. |

The published path is `plans/<epic>/`, derived from the epic id alone, so
revision two lands on top of revision one and **the link the CEO holds never
moves**. The source markdown is published beside the page — that is what makes
the next revision's `## Version record` generated rather than remembered.

The mockup renders as live markup, so it goes through an allowlist first: the
artifact is authored by an agent reading untrusted epic text, and the page is
opened in the CEO's browser. Styling and structure survive; scripts, frames,
event handlers and `javascript:` URLs do not, the check reports what it
removed, and the page carries a policy forbidding script execution outright.

Publishing is a **commit** to the portal repo, made by the bureau App
identity, which must therefore be installed there. Not the portal's "Add
document" button: that strips scripts, and an interactive mockup uploaded that
way renders looking complete while being dead.

## Every epic ends with a proof card and a demo card (DRE-2746)

The artifact's "Proof and demo" section states how the epic will be proven and
shown. Two **cards** are where it happens, and they are the epic's last two
children:

- **`PROOF: …`** answers *did it work* — and it is **not a green test suite**.
  It is the mechanism observed running against real state, with the observation
  recorded in the repo: what was read, when, and what it said. That record
  merges, so the card produces a written artifact rather than a claim.
- **`DEMO: …`** answers *can the CEO see it*. A merged PR and a passing suite
  are invisible to the person who green-lit the epic.

Both are blocked by every other child — the Linear `blocks` relation, never the
ordering and never a `**Blocked by:**` body line — and **neither may carry
`FLEET`**: a proof the fleet can close by merging its own code is not a proof.
The verdicts that MAY confirm an epic are derived from
`config/routing-verdicts.json` (the ones whose accountable actor is a human,
today `WORKBENCH` and `OPERATOR`), so the rule moves when the file does.

The convention was already written down and nothing made any planner follow it,
which is the point: **the check runs on the planner's output, not on the
brief's text.** `scripts/proof_and_demo.py` reads the cards the planner
actually created (`linear_ops.py children-detail`) and `plan.yml` bounces an
epic missing either card back to `Planning` with the reason named — on the
first pass and again after a revision. A crashed read posts nothing, because a
crash decided nothing.

## Shared CI plumbing (DRE-2550)

Product CI is product-specific — different stacks, different suites — but its
**plumbing** is not, and six independent copies of it is how a fix reaches one
repo and not the other five. What lives here is the machinery; what stays in
the product repo is which suites to run.

`.github/actions/setup-node-cached` sets up Node and puts `node_modules` in
place. Call it instead of writing your own `setup-node` + `npm ci` pair:

```yaml
# .github/workflows/ci.yml — CANARY REPOS ONLY (agent-bureau, bureau-pipeline)
- uses: actions/checkout@v5
- uses: dreadnought-foundry/bureau-pipeline/.github/actions/setup-node-cached@main
  with:
    working-directory: console/web
    node-version-file: console/web/.nvmrc   # or node-version: "24"
- run: npx vitest run
  working-directory: console/web
```

`.github/actions/setup-python-cached` (DRE-2589) is the same thing for Python:
it sets up Python and puts a virtualenv of the repo's **pinned** tooling in
place. Call it instead of writing your own `setup-python` + `pip install` pair:

```yaml
# .github/workflows/ci.yml — CANARY REPOS ONLY (agent-bureau, bureau-pipeline)
- uses: actions/checkout@v5
- name: Install test tooling (cached)   # name it — see below
  uses: dreadnought-foundry/bureau-pipeline/.github/actions/setup-python-cached@main
  with:
    python-version: "3.12"
    requirements: |
      requirements-dev.txt
- run: pytest tests        # the venv is on PATH for every later step
```

**Name the calling step.** GitHub's jobs API reports a composite action as
**one** step, under the *caller's* name — measured on run `32421767876`, whose
job returned only the caller's step names and never the action's own. So the
minutes report can only attribute cost to the name you write; an unnamed
`uses:` shows up as "Run ./.github/actions/setup-python-cached". (This holds for
`setup-node-cached` too.)

It takes requirements **files** and has no way to pass a package name. That is
the point: a `pip install pytest==9.1.0` in a job is a pin with a second home,
and installing bare names is what made a Dependabot bump exercise nothing
(DRE-2039). One manifest per repo, installed from that manifest.

`tests/test_ci_python_plumbing_once.py` is the guard: it fails if any workflow
here runs its own `pip install`, if a pin acquires a second home, or if a job
runs `pytest` without going through the action.

### `tdd-commit-check` — the test-first commit order, fleet-wide (DRE-2694)

`.github/actions/tdd-commit-check` runs `scripts/check_tdd_commits.py` — the
DRE-2022 rule that a commit touching `tests/` must appear **strictly before**
the first commit changing non-test code — from a product repo's own CI:

```yaml
# .github/workflows/ci.yml in a product repo
tdd:
  name: TDD commit discipline     # the check-run name the fix loop matches
  if: github.event_name == 'pull_request'
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v5
      with:
        fetch-depth: 0            # the check walks the PR's whole commit list
    - name: RED test precedes implementation
      uses: dreadnought-foundry/bureau-pipeline/.github/actions/tdd-commit-check@stable
      with:
        base-ref: ${{ github.event.pull_request.base.ref }}
        head-sha: ${{ github.event.pull_request.head.sha }}
        pr-author: ${{ github.event.pull_request.user.login }}
```

**Why it exists.** The checker was wired into this repo's `tests.yml` and
nowhere else. The fleet sweep of 2026-08-23 found the violation everywhere and
the detection in one place: portico #343 was **green and mergeable** with its
test committed after its code, and deltasolv runs no such check either. "Green"
across the fleet did not mean the order was right.

**Name the job `TDD commit discipline`.** GitHub publishes the check run under
the job name, and `scripts/unfixable_checks.py` matches on it (nested
`<caller> / <job>` names match too). A differently named job still gates the
merge; it just loses the fix loop's first-attempt escalation below.

**A red result has no automated path to green.** The finding is the ORDER of
commits that already exist, and the fix loop can add commits but not reorder
them. So `agent-fix.yml` consults the registry BEFORE it spends an attempt: it
posts a hold naming the check and the remedy, parks the card in the CEO's
"needs you" lane, and stops — on the first attempt, not the second. Clearing it
is a human reordering the existing commits and force-pushing; the content does
not change.

Adoption elsewhere needs a release tag containing this action (see "Which ref a
repo may use") plus a one-job addition to each repo's own `ci.yml` — per-repo
work, not something this repo can do to another.

### Which ref a repo may use — this is not a style choice

**`@main` is for the canary repos only.** `standards/engineering.md` is explicit:
*"the fleet consumes tagged releases (`vN`, paired `pipeline_ref`), never this
repo's live `main`; only agent-bureau and bureau-pipeline ride `@main` as the
canary channel."* That quote names `vN` because it predates DRE-2553; the
channel the fleet actually pins is now `@stable` (see "Release channel"
below). The half that matters here is unchanged either way — **`@main` is
still canary-only.** A composite action is no exception — it is consumed by the
product repo's CI on its very next run, so an unpinned reference means a bad
merge here reprograms that repo's CI with no canary soak and no promotion step.

**Every other repo pins to the channel ref its stubs are on** — `@stable`
today — exactly as those stubs do:

```yaml
# .github/workflows/ci.yml in a product repo — pinned, like its stubs
- uses: dreadnought-foundry/bureau-pipeline/.github/actions/setup-node-cached@stable
- uses: dreadnought-foundry/bureau-pipeline/.github/actions/setup-python-cached@stable
```

Unlike the internal checkouts inside a reusable workflow, this one takes **no
`pipeline_ref`** — a `uses:` reference cannot carry an expression. The ref is a
literal, so it must move in lockstep with the repo's workflow stubs and never
be left behind on `@main`. On a moving channel ref that costs nothing — the
tag advances and the reference follows; on a hand-cut `vN` it is part of the
operator's promotion step.

**No cut `vN` tag contains either action.** `v1`–`v5` were cut 2026-07-11 →
2026-07-21 and predate them, so `@v5` would not resolve to one. A product repo
therefore cannot adopt these until a release containing them is cut — which is
why Wave 1 sequences external adoption (Step 4) **after** automatic promotion
(Step 2). Adopting earlier would mean either an unpinned reference or a pin to
a tag that has no action in it.

Why it is not just `cache: npm`: `setup-node`'s own cache stores the
**downloads** (`~/.npm`), so a cache hit still pays the unpack and link on
every run — measured at 89s per web shard in `agent-bureau` against a warm
npm cache, 16% of that repo's billable CI minutes. This action caches
`node_modules` itself and skips `npm ci` outright on a hit.

Why it is not just `cache: pip`: same shape. `setup-python`'s own cache stores
`~/.cache/pip` — the downloads — so a hit still pays the resolve, the wheel
build and the install. `setup-python-cached` caches the virtualenv itself and
skips `pip install` outright on a hit. None of agent-bureau's three Python jobs
used even `cache: pip`, and because their install ran *inside* the test step,
its cost could not be separated from the suite's at all.

The **cache-break rule lives here and only here**: the key is the lockfile's
content plus the node version (for Python: every manifest's content plus the
**resolved** interpreter — a virtualenv is not relocatable across interpreters),
exact-match, no `restore-keys`. Don't reimplement it per repo — a partial
`node_modules` hit looks installed while holding another lockfile's packages, so
the suite fails far from the cause.

Two constraints worth knowing before you rely on it:

- **Caches are branch-scoped.** A cache saved on a PR branch is invisible to
  other branches; only `main` populates one for everybody. So a new adopter's
  own PR run is a MISS by design — the saving appears on the *next* PR.
- **A composite action must never check out this repo.** The outer `uses:` above
  is pinnable with a literal tag, but a checkout *inside* the action could not
  be — it would need `pipeline_ref`, and an action takes no `workflow_call`
  inputs — so it would escape the release channel entirely.
  `tests/test_shared_node_action.py` fails if one appears.

## Release channel: pinning, canary, promotion (DRE-2026)

The reusable workflows re-checkout this repo internally (into
`.bureau-pipeline/`) for their scripts, briefs, and standards. Those
checkouts thread the `pipeline_ref` workflow_call input
(`ref: ${{ inputs.pipeline_ref || 'main' }}`), so a pin only holds if the
stub sets BOTH halves:

```yaml
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/agent-task.yml@stable
    with:
      pipeline_ref: stable   # MUST match the ref on the uses: line
    secrets: inherit
```

**The pairing rule: whatever ref `uses: ...@<ref>` pins, `pipeline_ref` must
repeat.** The `uses:` ref pins only the top-level workflow YAML;
`pipeline_ref` pins everything that YAML checks out and executes
(scripts/briefs/standards). A stub that pins `uses:` but omits
`pipeline_ref` runs the pinned ref's YAML with `@main`
scripts — the exact chimera this channel exists to prevent. Omitting both
(plain `@main`, no input) is the rolling channel, unchanged.
`scripts/check_pipeline_ref.py` (a Pipeline Tests step, unit-tested in
`tests/test_pipeline_ref_threading.py`) fails CI here if any internal
checkout stops threading the input.

**Canary**: **agent-bureau is the designated canary and stays on `@main`**
(no `pipeline_ref`), so every merge here soaks on the canary's real traffic
before the fleet sees it. The fleet is one step behind the canary, on
`@stable` — see the `stable` section below for what the fleet actually
consumes.

**Promotion** (operator-only, never an agent): agents author, human
promotes, **harness proves** (DRE-2103). After a change has soaked on the
canary, the operator first runs the integration harness against the
candidate sha — the `pipeline_ref` input on `harness.yml` is exactly how a
candidate is tested pre-tag:

```bash
gh workflow run harness.yml --repo dreadnought-foundry/bureau-pipeline \
  -f pipeline_ref=<candidate-sha>
```

A green run stamps a success `integration-harness` commit status on the
sha it checked out (the stamp binds the TESTED sha — a dispatch run's own
head_sha records only the ref the workflow file was dispatched on). Only
then does the operator cut or re-point the next tag at that sha —

```bash
git tag -f v2 <candidate-sha> && git push origin v2 --force
```

— then re-points any stub pinned to the old tag (`@v1` → `@v2` together
with `pipeline_ref: v2`) repo by repo. `release-gate.yml` fires on every
`v*` tag push and goes loudly red when the tagged commit lacks a green
harness stamp (`scripts/release_gate.py`, fail-closed) — it cannot un-push
a tag, so a red run is the alarm to run the harness and re-point or drop
the tag. Rollback is the same move in reverse: re-point the stub back to
the previous tag pair (already-proved shas keep their stamps). Tags are not
PR-reviewable, so cutting/moving `vN` is deliberately a human step outside
the pipeline — and DRE-2551 left that exactly as it is.

**Scope of everything above: this is the operator's release path, not the
fleet's day-to-day channel.** No product repo has pinned a `vN` tag since
DRE-2553, so cutting one re-points nothing. `vN` is not dead — it is
the human-cut, human-moved release ref, and the harness command above is
still how any candidate sha earns its proof. What the fleet rides is the
next section. Read it before assuming otherwise.

**`stable`: proven automatically, and what the fleet pins (DRE-2551, then
DRE-2553).** There is now a second ref in this repo. `promote-channel.yml` keeps a moving tag,
`stable`, on the newest commit on `main` that carries a green
`integration-harness` stamp — `harness.yml` runs on every push to `main`,
so trunk commits have stamps of their own to read. The move is pushed with
the bot App identity, never `github.token`, precisely so `release-gate.yml`
(whose trigger now reads `["v*", "stable"]`) actually fires and validates
it; a repository variable, `CHANNEL_HOLD`, pauses promotion when set to a
reason, and unset means run. **Write that reason as
`who=<name> since=<ISO date> <why>`** — the staleness alarm below reports a
held channel back to the CEO, and GitHub will not tell it who set a variable
or when (that API needs an admin token the workflows do not carry), so a hold
that skips the convention is reported as a hold nobody will own.

**The fleet pins `@stable`, and has since 2026-08-23 (DRE-2553).** This
section used to say the opposite — *"no product repo pins `@stable`;
nothing consumes this ref"* — and that is recorded here rather than quietly
deleted. It stayed wrong for four days while five PRs contradicted it: the
wave that shipped this channel staled its own engine's front page
(`agent-bureau`, `architecture/post-mortems/wave-1-the-safety-rail.md` §8).

What this channel automated is the *proof*, never the release: cutting or
moving a `vN` tag is still **operator-only**, exactly as the promotion block
above describes. No repo consumes a `vN` tag.

Every consumer stub now rides `…@stable` **and** passes
`pipeline_ref: stable`. That input is **required** since DRE-2689 — a caller
must pass the same ref it pins in `uses:`, or it runs one ref's workflow YAML
over another ref's scripts, which defeats the pin entirely.

**This repo is the exception, deliberately.** Its own `uses:` are
fully-qualified `@main` self-callers, so a PR editing `merge-gate.yml`
cannot choose the logic that merges it — `@main` pins the gate to the
already-merged version. A standing decision, not an outstanding repointing.

**Which repo is on which ref is computed, never remembered here.** The
roster is `agent-bureau`'s `config/repo-map.json`; each repo's expected ref
and the reason for it live in `config/pipeline-channel.json`; and
`make check-channel-fleet` reads every repo live and compares the two. Ask
that command rather than trusting a number written into prose — the
sentence this replaced is what an enumeration looks like once the set has
moved (`adr-one-writer-per-fact`, DRE-2605).

The harness is also a PR gate here: `harness.yml` runs on pull requests
touching the boundary paths (workflow wiring + the dispatch/gate scripts),
and the merge gate's all-checks-green rule holds any boundary PR whose
harness run is red — no branch-protection change involved.

## Channel staleness alarm (DRE-2552)

Every other backstop in this repo watches for something **going wrong**.
`channel-watch.yml` watches for something **ceasing to happen** — because
that is the shape of the failure that hid July: the tag move did not break
and the gate did not fail, the mechanism simply stopped being invoked, and no
signal existed for "stopped". `main` ran 174 commits past `v5` over 29 days
and nothing said so.

The watcher runs daily, reads how far `main` is ahead of `stable` and how old
the channel head is, and raises **one** deduplicated Linear card (the
`red-main-repair.yml` / `model-drift.yml` pattern) — commenting on it daily
while the condition lasts rather than minting a new one. It holds
`contents: read` and cannot move the ref it watches. The decision, with the
full derivation, is `scripts/channel_watch.py`.

**The threshold, and where it came from.** Measured over `git log
origin/main` from the first commit (2026-06-11) to 2026-08-20 — 522 commits
across 70 days, 7.4/day:

| Fact | Value |
|---|---|
| gap between commits | p50 0.06h · p90 3.3h · p95 15.2h · p99 62.4h |
| longest quiet stretch ever | 257.9h = **10.7 days** |
| commits in a 72h window | median 32, tenth percentile 8 |

- **72 hours + 8 commits, both required.** 99.0% of observed gaps between
  commits are shorter than 72h, so ordinary quiet never reaches it; a rounder
  24h sits at the 96.9% mark and would fire most weekends. The **8 commits**
  is the tenth-percentile count for a 72h window: below it the trunk was
  unusually quiet and one slow harness run explains the lag, at or above it
  promotion has *stopped* rather than lagged.
- **14 days with a single unpromoted commit** — the backstop for a near-idle
  trunk, which the pair above would miss. Longer than any quiet stretch main
  has ever had (10.7 days above), so it cannot be ordinary.
- **A channel with nothing to promote is silent by construction** (`ahead ==
  0` never alarms). A noisy alarm gets muted, and a muted alarm on the thing
  protecting the fleet is how we get back to July.
- **24 hours for a hold.** A hold is true by construction, so this is a noise
  threshold, not a false-positive one: a switch flipped and cleared inside a
  working day stays private; one that outlives a day has blocked ~7 commits at
  this cadence and is a habit forming. A held channel is reported as **held —
  with who and when** — never as broken; unknown parts are printed as unknown
  rather than guessed.
- **A stale channel now names the commonest cause** (DRE-3070). The alarm
  fires on *not moving*, which is what a merge train produces, and it used to
  point at a promote-channel run log that in exactly that case held nothing:
  a displaced harness run triggered no promotion attempt at all. Every
  completed harness run now leaves a receipt naming one of
  `harness-passed-promoting` · `harness-run-not-on-main` ·
  `harness-cancelled-by-newer-push` · `harness-failed` · `channel-held` ·
  `no-harness-stamp` · `not-ahead-of-channel`, and the watcher counts the
  harness runs on `main` that concluded `cancelled` since the channel head.
  **Two or more and it says MERGE TRAIN**, not unknown — one skipped head is
  the queue-behind rule working (GitHub keeps a single pending run per
  concurrency group), two is merges arriving faster than the harness can prove
  them — and it adds whether a run is proving main right now. The alarm's
  *title* does not move with the diagnosis, so the card still dedups. See
  `docs/self-hosting.md`, "Queue behind, never cancel".
- **Where the hold's age comes from** (DRE-2603). The repository-variables API
  needs admin scope and answers 403 to the workflow's token, so its answer is
  used only when it *is* a timestamp — an error body is a failed read, never a
  value, and is never rendered as a date. Failing that, the age is measured
  from the `since=` the operator wrote into `CHANNEL_HOLD`. With neither, the
  age is unknown and an unknown-age hold still alarms: failing loud is right,
  it just must not be the only path, or the 24h above never applies.

**So what if the watcher stops?** Two answers, and only one of them is
mechanical:

- **A red run is diagnosed.** `Channel Watch` is in the medic's watch list
  (`self-medic.yml`), like every other runnable workflow here.
- **A skipped run is reported by the next one.** Each run reads its own last
  completed run and alarms if it missed more than two ticks.
- **A watcher that stops for good is not detected by anything in this repo.**
  Nothing polls for its absence. That is stated rather than papered over —
  adding a sixth mechanism nobody checks would be Wave 0's mistake at one
  more level of indirection.

## Layout

- `.github/workflows/` — the reusable workflows (must live here for
  `workflow_call` to resolve them)
- `.github/actions/` — composite actions the product repos' own CI calls, for
  plumbing that should exist once rather than six times (DRE-2550). Pinned by a
  literal tag in the caller's `uses:` line, moved by the promotion step; NOT by
  `pipeline_ref`, which is a `workflow_call` input an action cannot receive.
  For that reason they must never check this repo out — see above.
- `scripts/linear_ops.py` — Linear CLI (stdlib only); `scripts/reconcile.py`
  imports it as a sibling. Jobs check this repo out into `.bureau-pipeline/`
  inside the product checkout and call
  `python3 .bureau-pipeline/scripts/<x>.py`.
- `standards/` — the canonical shared rules, injected per role by
  `scripts/assemble_context.py`. See `standards/README.md` for the mapping.
- `briefs/` — agent role briefs (engineer, planner). Generic by design:
  repo-specific facts belong in the product repo's
  `.github/bureau/overrides.md`.

## Onboarding a new product repo

1. Copy another product repo's eight stub workflows; adjust the
   `workflow_run` lists in `medic.yml`/`merge-gate.yml`/`red-main-repair.yml`
   to include the repo's own CI workflow names — **every** workflow that runs
   on a push to the default branch belongs on the repair stub's list, not just
   the one you think of first (DRE-2820; `python3
   scripts/check_workflow_watchers.py <the repo's .github/workflows>` answers
   it from the files). Then run
   `python3 scripts/fix_concurrency.py audit .github/workflows` against the new
   stubs — the `agent-fix` group is the one a copy gets silently wrong
   (DRE-2810).
2. Write `.github/bureau/overrides.md` (and `setup.sh` if agents need an
   environment beyond a bare runner).
3. Set the six secrets, install both bureau Apps, and register the repo slug
   with the relay Lambda.
4. Add the `tdd-commit-check` job to the repo's own `ci.yml`, named exactly
   `TDD commit discipline` (the snippet is under **Shared CI plumbing**). Skip
   it and the repo absorbs the test-first violation silently: the 2026-08-23
   sweep found portico #343 green and mergeable with its test committed after
   its code, because the check ran in one repo and the discipline was broken in
   four. The job name is load-bearing — `scripts/unfixable_checks.py` matches
   the published check-run name to escalate the fix loop on its first attempt.
