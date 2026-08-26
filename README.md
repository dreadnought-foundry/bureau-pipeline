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

## Build by default; escalate by exception (DRE-1655)

The engineer agent (`agent-task.yml` + `briefs/engineer.md`) is **autonomous by
default**: it researches a card and, when confident, builds and ships it through
the normal PR → critic → merge gates with no human in the loop. It **stops and
asks only by exception** — on genuine uncertainty (ambiguous intent, a
risky/destructive change, or a business A-vs-B decision). When it stops it posts
a **plain-English question** as a Linear comment and parks the card in the
`Triage` lane (the lane for a card that went wrong — DRE-2722/2723; the epic
approve-the-plan queue is `Green Light`, a different job); the CEO answers and
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
it. Only the human "needs you" queue (now `Triage`) and the console surfacing
of it are reused from the propose design.

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
| `secrets: inherit` + `with:` inputs (`pipeline_ref` everywhere; `max_wip` on all three promotion paths) | `secrets:`/`inputs:` declarations |

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

## One WIP cap per repo (DRE-2529)

A repo has ONE work-in-progress cap, and **three** workflows promote Backlog
children through the dependency gate at it:

| workflow | when it promotes |
|---|---|
| `reconcile.yml` | the `*/15` cron sweep (GitHub delivers it 78–100 min apart in practice) |
| `plan.yml` | the moment an epic is activated |
| `linear-sync.yml` | the moment a merge marks a card Done |

The last two are the anti-stall fast paths: they exist purely to promote work
*now* instead of up to ~80 minutes later. They used to hardcode `MAX_WIP=8`
while `reconcile.yml` took the caller's value — so on a repo capped at 12,
between 8 and 11 cards in flight both fast paths refused and the cron sweep
promoted the same card an hour later at 12. The optimisation switched itself
off exactly inside the band it was built for, and nothing reported it.

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
- `briefs/` — agent role briefs (engineer, planner). Generic by design:
  repo-specific facts belong in the product repo's
  `.github/bureau/overrides.md`.

## Onboarding a new product repo

1. Copy another product repo's eight stub workflows; adjust the
   `workflow_run` lists in `medic.yml`/`merge-gate.yml` to include the repo's
   own CI workflow names.
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
