# Card-quality standard — the Linear card contract

The human-readable contract for how a Linear card must be structured to flow
through the pipeline. **The live enforcer is `scripts/validate_card.py`** (the
Todo-entry gate) and its tests — that code is the real single source of truth;
this is the standard it implements. Create cards with the Linear MCP
(`save_issue`) or `scripts/linear_ops.py`. **Search before you create** to avoid
duplicates.

## Required — a card is valid with BOTH
1. A **`repo:<slug>` label** (slug ∈ the relay's `VALID_SLUGS`) — the canonical
   source of truth for the card's repo. *(Legacy fallback: a `**Repo:** <slug>`
   frontmatter line in the description is still ACCEPTED for pre-existing cards
   so they keep routing, but it's deprecated — set the label, don't write the
   stamp. Fenced code is ignored.)*
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

## Lifecycle — build by default; escalate by exception (DRE-1655)
A card flows `Todo → In Progress → In QA → In Review → Done`, **unattended**.
The engineer agent is **autonomous by default**: it researches the card and, if
confident, builds and ships it through the normal PR → critic → merge gates — no
human in the loop (overnight automation is the point). The adversarial critic
and the test suite are the correctness backstop, so the CEO is not gating every
diff.

The agent **stops and asks only by exception** — on genuine uncertainty it
cannot safely resolve: **ambiguous intent**, a **risky/destructive change**, or
a real **business A-vs-B decision** the CEO should own. When it stops, it posts a
**plain-English question** (business terms, no code or diffs) as a comment and
parks the card in the **`Plan Review`** lane (the "needs you" queue; the same
lane epics use for plan approval). The CEO answers and moves the card back to
`Todo` to proceed (a fresh run picks up the guidance) or to `Backlog` to drop
it. This is a **high bar** — over-escalating recreates the overnight-stall the
model exists to avoid; routine, reversible choices are just built and noted in
the PR.

`Plan Review` (decision needed, build can proceed once answered) is distinct
from `Backlog` (the impossible-as-specified / blocked path, inert until the card
is fixed). There is **no propose-first hard stop**: cards are not gated awaiting
approval before any work — autonomy is the default, the human is the exception.

## Epics
Expressed by Linear **native parent/child** (not a label, not frontmatter).
`[EPIC]` in the title OR having children ⇒ the gate infers `agent:planner`. The
epic's **first prose paragraph** is the CEO-readable plan summary — lead with it
(the repo is carried by the `repo:<slug>` label, not a body line). To start an
epic, **move ONLY the epic to In
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
