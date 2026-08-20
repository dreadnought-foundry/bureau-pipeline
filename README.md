# bureau-pipeline

The Agent Bureau's pipeline, defined once. Every product repo
(EveryBite/atlas, dreadnought-foundry/deltasolv, dreadnought-foundry/vericorr,
…) runs the SAME eight workflows from here via GitHub reusable workflows.
A change merged to `main` in this repo is live in every product repo on its
next trigger — `@main` is the rolling channel. For fleet repos that want
change isolation there is a **tagged release channel** (`v1`, `v2`, …, cut by
the operator): a stub pins `uses: ...@vN` **and** passes
`pipeline_ref: vN`, and moves only when the operator re-points it. See
"Release channel" below.

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
`Plan Review` lane (the "needs you" queue, reused from epic plan approval); the
CEO answers and moves it back to `Todo` to proceed or to `Backlog` to drop it.
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
it. Only the `Plan Review` lane and the console "needs you" surfacing are
reused from the propose design.

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
| `secrets: inherit` + `with:` inputs (`pipeline_ref` everywhere; `max_wip` on reconcile) | `secrets:`/`inputs:` declarations |

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

### Which ref a repo may use — this is not a style choice

**`@main` is for the canary repos only.** `standards/engineering.md` is explicit:
*"the fleet consumes tagged releases (`vN`, paired `pipeline_ref`), never this
repo's live `main`; only agent-bureau and bureau-pipeline ride `@main` as the
canary channel."* A composite action is no exception — it is consumed by the
product repo's CI on its very next run, so an unpinned reference means a bad
merge here reprograms that repo's CI with no canary soak and no promotion step.

**Every other repo pins to the release tag**, exactly as its reusable-workflow
stubs do:

```yaml
# .github/workflows/ci.yml in a product repo — pinned, like its stubs
- uses: dreadnought-foundry/bureau-pipeline/.github/actions/setup-node-cached@v6
```

Unlike the internal checkouts inside a reusable workflow, this one takes **no
`pipeline_ref`** — a `uses:` reference cannot carry an expression. The ref is a
literal, so **repointing it is part of the promotion step**, moved in lockstep
with the workflow stubs and never left behind on `@main`.

**No existing tag contains this action.** `v1`–`v5` were cut 2026-07-11 →
2026-07-21 and predate it, so `@v5` would not resolve to it. A product repo
therefore cannot adopt this until a release containing it is cut — which is why
Wave 1 sequences external adoption (Step 4) **after** automatic promotion
(Step 2). Adopting earlier would mean either an unpinned reference or a pin to
a tag that has no action in it.

Why it is not just `cache: npm`: `setup-node`'s own cache stores the
**downloads** (`~/.npm`), so a cache hit still pays the unpack and link on
every run — measured at 89s per web shard in `agent-bureau` against a warm
npm cache, 16% of that repo's billable CI minutes. This action caches
`node_modules` itself and skips `npm ci` outright on a hit.

The **cache-break rule lives here and only here**: the key is the lockfile's
content plus the node version, exact-match, no `restore-keys`. Don't
reimplement it per repo — a partial `node_modules` hit looks installed while
holding another lockfile's packages, so the suite fails far from the cause.

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
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/agent-task.yml@v1
    with:
      pipeline_ref: v1   # MUST match the tag on the uses: line
    secrets: inherit
```

**The pairing rule: `uses: ...@vN` must pair with `pipeline_ref: vN`.**
The `uses:` ref pins only the top-level workflow YAML; `pipeline_ref` pins
everything that YAML checks out and executes (scripts/briefs/standards). A
stub that pins `uses:` but omits `pipeline_ref` runs vN YAML with @main
scripts — the exact chimera this channel exists to prevent. Omitting both
(plain `@main`, no input) is the rolling channel, unchanged.
`scripts/check_pipeline_ref.py` (a Pipeline Tests step, unit-tested in
`tests/test_pipeline_ref_threading.py`) fails CI here if any internal
checkout stops threading the input.

**Canary**: the fleet consumes the current `vN` tag. **agent-bureau is the
designated canary and stays on `@main`** (no `pipeline_ref`), so every
merge here soaks on the canary's real traffic before the fleet sees it.

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

— then re-points fleet stubs (`@v1` → `@v2` together with
`pipeline_ref: v2`) repo by repo. `release-gate.yml` fires on every `v*`
tag push and goes loudly red when the tagged commit lacks a green harness
stamp (`scripts/release_gate.py`, fail-closed) — it cannot un-push a tag,
so a red run is the alarm to run the harness and re-point or drop the tag.
Rollback is the same move in reverse: re-point the stub back to the
previous tag pair (already-proved shas keep their stamps). Tags are not
PR-reviewable, so cutting/moving `vN` is deliberately a human step outside
the pipeline — and DRE-2551 left that exactly as it is. Read the next
paragraph before assuming otherwise.

**`stable`: proven automatically, pinned by nobody (DRE-2551).** There is
now a second ref in this repo. `promote-channel.yml` keeps a moving tag,
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
that skips the convention is reported as a hold nobody will own. What
automatic promotion deliberately does **not** do:

- **No product repo pins `@stable`.** Nothing consumes this ref. No stub
  was re-pointed, and the pairing rule above is untouched — the fleet is on
  `vN` with a matching `pipeline_ref: vN`, exactly as before.
- **`vN` promotion is unchanged and remains operator-only**: the `git tag
  -f` and the stub re-point above are still a human's job, and still the
  gate between a merge here and anything the fleet runs.

Its value today is that the pre-tag question — *which sha has the harness
proved?* — is answered continuously instead of by a hand-run dispatch; the
operator still picks the soaked sha and still cuts it. Whether the
fleet ever pins `stable` directly and the manual cut retires is a **later
decision, not part of DRE-2551**; until it is made and written here, `vN`
is the only channel the fleet consumes.

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
