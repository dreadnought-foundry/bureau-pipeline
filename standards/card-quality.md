# Card-quality standard — the Linear card contract

The human-readable contract for how a Linear card must be structured to flow
through the pipeline. **The live enforcer is `scripts/validate_card.py`** (the
Todo-entry gate) and its tests — that code is the real single source of truth;
this is the standard it implements. Create cards with the Linear MCP
(`save_issue`) or `scripts/linear_ops.py`. **Search before you create** to avoid
duplicates.

## Required — a card is valid with BOTH
1. **`**Repo:** <slug>`** as a frontmatter line in the description (slug ∈ the
   relay's `VALID_SLUGS`) — **OR** a `repo:<slug>` label. Frontmatter wins;
   fenced code is ignored.
2. An **`agent:*` label** (`agent:engineer`, `agent:frontend`, `agent:devops`,
   `agent:planner`, …).

The Todo gate is **fix-first**: it auto-repairs a missing piece when it can infer
it (from an `initiative:<x>` label or the Linear project-name prefix) and only
**bounces** to Backlog when the repo can't be inferred deterministically. Get it
right and the gate is a no-op.

## Optional — only when applicable
- **`**Design:** <png path>`** — UI cards ONLY (e.g.
  `console/design/images/screens/desktop/board.png`). **Forbidden** on non-UI
  cards; its absence is normal. (See `standards/design.md`.)
- **`**Spec:** openspec/changes/<id>/`** — only when the work needs a
  cross-component contract; read it before coding.
- **`**Blocked by:** DRE-N, DRE-M`** — a body line the console parses into the
  dependency gate. **Never name the parent epic here** (epics stay In Progress →
  deadlock). Also set the Linear formal `blockedBy` relation — that relation is
  the source of truth the reconcile/auto-close gates honor; prose is not.
- **Labels:** `initiative:<x>` (the cross-project filter); `no-code` for
  operator/non-build cards.

## Epics
Expressed by Linear **native parent/child** (not a label, not frontmatter).
`[EPIC]` in the title OR having children ⇒ the gate infers `agent:planner`. The
epic's **first prose paragraph** is the CEO-readable plan summary — lead with it
(after the `**Repo:**` line). To start an epic, **move ONLY the epic to In
Progress and stop** — reconcile auto-promotes the unblocked children; never
hand-move children (it double-dispatches and reverts in-progress work).

## Body
A clear, **one-PR-scoped** description with its own `## Acceptance criteria`
(checkable `- [ ]` items). Any string shared across sibling cards (schema field,
route, type, env var) is written **identically** in both — that string is a
contract; the planner greps `main` first to confirm the name is free.

## Dead — do not use
The 8-section XML tags, `**Size:**`, and `scripts/orch/v4` references — v1
conventions the cloud pipeline ignores.
