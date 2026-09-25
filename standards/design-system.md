# Design-system standard — one `design/` folder, one lock, one critic

Every repo's design work lives in the same shape and is judged by the same
checklist. The CEO decided this on **2026-09-14**; the decision record is
[`architecture/decisions/adr-design-system-and-new-roles.md`](https://github.com/dreadnought-foundry/agent-bureau/blob/main/architecture/decisions/adr-design-system-and-new-roles.md)
in agent-bureau. The Designer role there already defines "done" as the checklist
below passing — this file is where the agents that build design work and the
critics that gate it actually read it.

`standards/design.md` sets the craft bar for one screen; `design-parity.md`
makes a set of cards sum to the design. This is the layer under both: WHERE the
design lives, WHAT locks it, and WHO says it is done. A repo- or console-scoped
design-critic lens cites this file rather than restating it.

## The folder — one shape, every repo

One `design/` folder at the repo ROOT, with exactly these entries:

    design/
      README.md          how this repo's design work is organised, and where to start
      DESIGN.md          the design itself — decisions, rationale, current state
      tokens/            every value: color, type, space, radius, elevation, motion
      atoms/             the smallest components; nothing composed
      molecules/         composed from atoms only
      organisms/         composed from molecules and atoms only
      templates/         page-level composition; the master template lives here
      screens/web/       concrete web screens, built from templates
      screens/mobile/    concrete mobile screens, built from templates
      brand/             brand assets and the per-product brand config
      decks/             presentation decks
      collateral/        one-off marketing and sales assets
      emails/            transactional and marketing email designs
      handoff/           what an engineer builds from: extracts, specs, exports
      LOCK.json          the locked tokens and atoms — the CEO's agreement, recorded

**Atomic composition, in that order.** An atom composes nothing.
A molecule composes atoms; an organism composes molecules and atoms; a template
composes organisms; a screen is a template with content. A layer that reaches
past the one below it is a violation, not a shortcut.

## The lock rule

`tokens/` and `atoms/` change **only on the CEO's agreement**, and the agreement
is **recorded in `LOCK.json`** — the locked token set, the locked atom set, and
the date agreed. `LOCK.json` IS the record, so a token or atom
that is not in it is not locked, and a design that uses one is not done.

**What counts as his agreement, and who writes it down**, the CEO settled on
**2026-09-24**: *"let's remove this restriction going forward, if I approve the
design that is all I need to do."* (DRE-4833.) Five points, and they are the
rule:

1. **Approving a design IS the agreement.** When the CEO approves a design — a
   mockup, an artifact, a screenshot, or his answer on one in a session — that
   approval covers **every token and atom addition or change the design shows**.
   Nobody asks him again per atom, and no operator decision is brought to him
   for one.
2. **The builder records it, in the same PR.** The builder writes the entry —
   his words, the date, and where he said it — citing the design approval: in
   `LOCK.json` for the fleet shape above, or in the repo's own approval record
   (Portico's `atoms.approved.test.ts`). Recording is the builder's
   bookkeeping, not a step the CEO takes.
3. **The critic accepts a recorded design approval.** A PR is not held because
   the approval was recorded by its own builder. The critic checks that the
   atom change **is what the approved design shows**; it does not check who
   typed the record.
4. **A card cannot forbid what an approved design needs.** A card's "no file in
   `atoms/` changes" criterion yields to an approved design that changes one,
   and card authors do not write that criterion where a design is approved.
5. **What stays gated:** a token or atom change **no approved design shows**.
   The remedy is to show him the design, which is still the one step.

Origin: Portico PR #697 (DRE-4757) was held twice over an atom change the CEO
had asked for, because the card said no atom changes and the only record of his
OK was the builder's commit message. He had to be asked again, and an operator
decision posted, before it could move. Points 1-4 remove that second step.

Everything above atoms moves freely: molecules, organisms, templates and screens
are composed out of the locked set and need no fresh agreement.

## The multi-brand rule

Multi-brand is **shared components plus a per-product token/brand and feature
config** — never a forked component set. The components live once; each product
supplies its own token file, its own `brand/` config, and its own feature config.
A component that branches on the product name is that fork wearing a disguise.

## Starting from the master template

**New apps and new designs start from the master template** in
`design/templates/`. A design that starts from a blank page, or from a copy of
another product's screen, is rejected. Starting from the template is what makes
the fleet's design work one system rather than several that happen to share a
palette.

## The design critic's checklist — what gates "done"

Design work is done when a reviewer answers **yes to every item**, and answers
it **from the files alone**. Each item is a file question on purpose: a check
that needs a live tool, a login, or a person's eye is not a gate, it is a hope.

1. **Structure and README followed** — the folder is the shape above, and the
   work sits where `design/README.md` says it does.
2. **Every value is a token** — no literal color, size, space, radius, shadow or
   duration anywhere outside `tokens/`.
3. **No atom that is not in `LOCK.json`** — every atom the work uses appears in
   the locked atom set.
4. **Molecules and organisms composed from locked atoms** — each composed
   component resolves down to locked atoms, with no unlocked primitive and no
   layer skipped.
5. **Complete, including states and widths** — every state the surface can be in
   (default, hover, focus, active, disabled, loading, empty, error) and all three
   widths: phone, tablet, desktop.
6. **It renders, and passes contrast and accessibility** — the file opens with no
   missing asset and no unresolved token reference; every foreground/background
   token pair it uses meets WCAG AA (4.5:1 body text, 3:1 large text and UI
   boundaries) computed from the values in `tokens/`; and every interactive
   element carries an accessible name and a visible focus state.
7. **No divergence from the master template** — work that diverges from
   `design/templates/` is rejected, not noted.

A failed item is a **blocking finding**, the same grade as a missing test.

## Not on the checklist, deliberately

**"The published Claude Design project matches the locked files" is NOT a check
here, and its absence is a decision, not an omission.** The CEO dropped it from
the file-based checklist on 2026-09-16: it **cannot be answered from the files**,
and **the Designer verifies it by hand after each lock**.

It is written down because the ADR still describes the published project, so a
reader who finds this checklist shorter than the ADR would reasonably conclude
the item was forgotten. It was not. **Do not reinstate it** — a checklist item no
reviewer can actually answer is either skipped or guessed, and a guessed gate is
worse than a gate that is honestly somewhere else.
