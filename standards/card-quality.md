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
  dependency gate. It must **open its own line** (`Blocked by:` /
  `Serialize after:` / `Depends on:`, optionally inside a list item — bulleted
  or numbered — or bold markup) — the gate reads a declaration, never a
  mention, so an ordinary sentence that happens to say "blocked by" or
  "depends on" is just prose
  (DRE-2670: epic DRE-2492 froze five of its own children for five days on the
  sentence *"neither depends on the other"*). **Never name the parent epic
  here** (epics stay In Progress → deadlock). Also set the Linear formal
  `blockedBy` relation — that relation is the source of truth the
  reconcile/auto-close gates honor; prose is not, and an epic held by prose
  alone now says so in the sweep log.
- **Labels:** `initiative:<x>` (the cross-project filter); `no-code` for
  operator/non-build cards.
- **`break-glass`** — the ONE sanctioned way past the Todo-entry gate at 2am
  (DRE-2737). **Operator-only**: applied by hand, in Linear, by a person; the
  pipeline's own label writes refuse it, so **no agent may apply it**. The
  bypass is recorded on the card and counted rather than undone, and the card
  owes the classification it skipped — once its work merges it returns to
  `Planning` for that review instead of going Done. Removing the marker
  afterwards changes neither the record nor the debt.

## Lifecycle — build by default; escalate by exception (DRE-1655)
A card flows `Todo → In Progress → In Review → Done`, **unattended** — one
review lane since DRE-2726, because the two that preceded it both meant "a pull
request is open and being checked". The lane contract is data
(`config/lane-contract.json`) and `docs/lane-contract.md` is rendered from it.
The engineer agent is **autonomous by default**: it researches the card and, if
confident, builds and ships it through the normal PR → critic → merge gates — no
human in the loop (overnight automation is the point). The adversarial critic
and the test suite are the correctness backstop, so the CEO is not gating every
diff.

The agent **stops and asks only by exception** — on genuine uncertainty it
cannot safely resolve: **ambiguous intent**, a **risky/destructive change**, or
a real **business A-vs-B decision** the CEO should own. When it stops, it posts a
**plain-English question** (business terms, no code or diffs) as a comment and
parks the card in the **`Green Light`** lane — the CEO's "needs you" queue,
the same lane epics wait in for plan approval. The CEO answers and moves the
card back to `Todo` to proceed (a fresh run picks up the guidance) or to
`Backlog` to drop it.

**NOT `Triage`, and the distinction is the point.** Triage is the *broken-card*
lane: an unroutable `repo:` label, an archived repo, a card the readiness guard
has returned three times — mechanically wrong, usually an agent or operator fix.
An escalated card is **not broken**. It is correct, and waiting on a judgement
only the CEO can make. Triage became a dead end once by mixing the two — 17
cards, all machine-created, none ever moved — and a real decision sitting in a
lane people scan as a defect list is that same failure wearing a new label
(DRE-2776). DRE-2722's title reads "move the escalations to Triage"; the
criteria it was accepted against say `Green Light` holds what the lane it
renamed held, and that lane held escalations. A title is not what a card was
accepted against.

Escalating is a **high bar** — over-escalating recreates the overnight-stall
the model exists to avoid; routine, reversible choices are just built and noted
in the PR.

`Green Light` (decision needed, build can proceed once answered) is distinct
from `Backlog` (the impossible-as-specified / blocked path, inert until the card
is fixed), and from `Triage` (the card itself is malformed and cannot proceed as
written, whoever answers).

There is **no propose-first hard stop**: cards are not gated awaiting
approval before any work — autonomy is the default, the human is the exception.

## Epics
Expressed by Linear **native parent/child** (not a label, not frontmatter).
`[EPIC]` in the title OR having children ⇒ the gate infers `agent:planner`. The
epic's **first prose paragraph** is the CEO-readable plan summary — lead with it
(the repo is carried by the `repo:<slug>` label, not a body line). To start an
epic, **move ONLY the epic to In
Progress and stop** — reconcile auto-promotes the unblocked children; never
hand-move children (it double-dispatches and reverts in-progress work).

**A card has no children.** Giving a card sub-issues silently converts it INTO
an epic — the gate infers `agent:planner` from having children, and reconcile
never promotes an `agent:planner` card — so the parent stops being promoted,
permanently, with nothing saying so. `linear_ops.py subissue` refuses a parent
that is not already an epic. Neither a `DRE-1234a` suffix nor a sub-issue: the
new work is a **sibling card under the same epic**, with its own number.

## Mid-epic discovery (DRE-2739)
A finding made **while building**, about an epic that is already approved and
already running, does not go back to Intake. It is filed against the epic with
one line of justification, classified by one question — **does the approved plan
still describe what we are doing?**

    python3 scripts/mid_epic.py discovery <EPIC> --kind addition \
      --because "<one line>" --title "…" --body <file>
    python3 scripts/mid_epic.py discovery <EPIC> --kind amendment --because "<one line>"

- **Addition** — the plan holds, there is just more of it (a second call site
  needs the same fix). A sibling card lands in Backlog carrying a **verdict**
  and promotes normally. **No new green light**: that decision was already made
  for this epic.
- **Amendment** — the plan no longer describes the work (the fix must be split
  and its order reversed). No card is created; the epic goes back to `Planning`
  and is re-green-lit.

Guessing wrong is cheap — the artifact update catches an amendment mislabelled
as an addition. What is NOT cheap is adding the card by hand: a Backlog child of
an active epic dispatches an agent within fifteen minutes, so a card added after
the epic's green light **without a verdict is refused promotion** and said so on
the card. The epic's own description carries the growth record — green-lit at N
cards, running M — and names any card that joined without the plan moving with
it. See `architecture/decisions/adr-mid-epic-discovery.md`.

## Body
A clear, **one-PR-scoped** description with its own `## Acceptance criteria`
(checkable `- [ ]` items). Any string shared across sibling cards (schema field,
route, type, env var) is written **identically** in both — that string is a
contract; the planner greps `main` first to confirm the name is free.

## Dead — do not use
The 8-section XML tags, `**Size:**`, and `scripts/orch/v4` references — v1
conventions the cloud pipeline ignores.
