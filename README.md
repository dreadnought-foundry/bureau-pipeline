# bureau-pipeline

The Agent Bureau's pipeline, defined once. Every product repo
(EveryBite/atlas, dreadnought-foundry/deltasolv, dreadnought-foundry/vericorr,
…) runs the SAME eight workflows from here via GitHub reusable workflows.
A change merged to `main` in this repo is live in every product repo on its
next trigger — `@main` is the rollout channel. There are no versioned tags;
if a change is risky, test it by pointing one repo's stub at a branch ref
first.

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
| `secrets: inherit` + `with:` inputs (`max_wip` on reconcile) | `secrets:`/`inputs:` declarations |

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
