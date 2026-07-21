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
`propose.yml` workflow and its marker are gone; the relay routing was canceled
and never deployed. Only the `Plan Review` lane and the console "needs you"
surfacing are reused.

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
PR-reviewable, so cutting/moving them is deliberately a human step outside
the pipeline.

The harness is also a PR gate here: `harness.yml` runs on pull requests
touching the boundary paths (workflow wiring + the dispatch/gate scripts),
and the merge gate's all-checks-green rule holds any boundary PR whose
harness run is red — no branch-protection change involved.

## Layout

- `.github/workflows/` — the reusable workflows (must live here for
  `workflow_call` to resolve them)
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
