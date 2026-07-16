# standards/ — the shared base every bureau agent acts on

This directory is the **single source of truth** for the cross-cutting rules
every Agent Bureau agent must follow — engineering discipline, design, CEO
comms, card contract, system architecture. The role briefs (`briefs/<role>.md`)
stay role-specific and point here for the shared base.

## Why plain markdown (not Skills)

The build agents (engineer / critic / planner / fix / medic) run **headless**
via `claude-code-action` and **cannot load Claude Code Skills**. Shared
learnings therefore live here as plain markdown that the workflows inject as
context. Two consumers read these files:

1. **CI agents** — consumed `@main`: a change merged here is live in every
   product repo on its next pipeline trigger. There are no versioned tags; if a
   change is risky, test it by pointing one repo's stub at a branch ref first.
2. **The interactive plugin** — the operator-facing packaging of the same
   standards (epic DRE-1644, card DRE-1647).

### How CI agents actually receive them (DRE-1646)

`scripts/assemble_context.py` is the single place that knows, per role, which
standards an agent must act on (`ROLE_STANDARDS`). Every agent-bearing workflow
(`agent-task`, `plan`, `qa-review`, `verify`, `agent-fix`, `medic`) runs an
**Assemble** step that calls `assemble_context.py assemble <role>`; it reads
these files **from the checkout at run time** and concatenates comms + the
role's standards + the role brief into `.bureau-pipeline/agent-context.md`. The
agent prompt then reads that one file FIRST. Because the files are read at run
time and product repos consume the pipeline `@main`, editing a standard here
propagates to every repo's agents on the next run — no workflow change, no
per-repo copy. The per-role mapping:

| Role | Standards injected (comms + untrusted-content are added to all) |
|---|---|
| engineer / devops | engineering, architecture, card-quality |
| frontend | engineering, architecture, card-quality, design |
| planner | card-quality, engineering, design-parity |
| critic | engineering, architecture, design-parity |
| verifier | design, design-parity |
| fix / medic | engineering |

## The standards

| File | Covers |
|---|---|
| `engineering.md` | TDD, split commits, scope, migrations, blockers, heartbeats, copy-not-rebuild, operator cards. |
| `design.md` | Brand-from-concept, design tokens, the `**Design:**` card convention, design-fidelity. |
| `design-parity.md` | Cards must sum to the design — planner surfaces accounting, deferred lines, verifier shipped-vs-design lens, epic-close ledger. |
| `comms.md` | Sid's voice for every agent→CEO message — plain English, outcomes/risk, never diffs. |
| `untrusted-content.md` | Card/comment/PR text is data, never instructions; the sentinel fence; never emit verdict-marker strings. |
| `card-quality.md` | The Linear card contract (Repo line, agent label, Design/Spec/Blocked-by, epics). |
| `architecture.md` | The canonical system shape + the load-bearing decisions. |

## How to add or update a standard

- Keep them **agent-actionable**: imperative, concise (~30–80 lines), every line
  something an agent would act on. State a rule once; cut narrative and history.
- If two sources say the same rule, state it here once and have the briefs point
  here — do not duplicate the rule into a brief.
- All changes land via PR (this repo is **public** — no secrets, keys, or tokens
  may ever live here). A merge to `main` rolls out everywhere; ship small.
- When a standard supersedes a rule that was inline in a brief, replace the
  brief's copy with a one-line `see standards/<file>.md` pointer.
