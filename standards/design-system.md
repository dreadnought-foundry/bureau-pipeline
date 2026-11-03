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

**Amended on 2026-11-01** (DRE-8708, rule 6). This is a deliberate change,
dated 2026-11-01, not a drift: until that day only the CEO's agreement could
add an atom. The CEO approved it with the shared-parts rule below, because he
does not want work stopping for him. Three things now hold, and the five points
above stay word for word:

- **A critic's recorded approval admits a provisional part.** A
  `🪴 part-provisional:` line in the critic's APPROVE verdict admits a
  provisional part, an atom included, into the system with no fresh
  agreement (rule 5, in
  [UI code in a repo whose design system exists](#ui-code-in-a-repo-whose-design-system-exists)).
- **The CEO can overrule it afterward.** He sees every provisional part on his
  weekly list and can reject it. A rejection removes the part and files a card
  to replace its uses, and two weeks with no rejection make it permanent
  (rule 11).
- **A design the CEO approved still counts as his agreement.** The 2026-09-24
  rule above is unchanged.

A token is not a part, so a token change still moves only as the five points
say.

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

The checklist covers design work in the `design/` folder. UI code, the
components and screens a product ships, is judged by
[UI code in a repo whose design system exists](#ui-code-in-a-repo-whose-design-system-exists).

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

## UI code in a repo whose design system exists

The checklist above judges design work in `design/`. This section judges the
production UI code an agent builds: every screen uses the product's shared
parts (its atoms, molecules, organisms and templates), and a second copy of a
part never gets in. The CEO approved it on **2026-11-01**. Its rules 1 to 11
are settled in epic DRE-8708, and this section states them and cites the epic
rather than re-deriving them.

Origin: on 2026-10-30 a second local `function Notice` reached agent-bureau's
initiatives page, a copy of the console's shared `Notice` molecule built from
tokens only, and nothing went red (DRE-8531).

### Scope (rule 1)

The rule applies to any pull request that changes a UI file in a repo whose
design system exists. A UI file is any file with the extension `.tsx`, `.jsx`,
`.vue` or `.svelte` (`design_parts.UI_EXTENSIONS`).

"Exists" is read from files, never remembered. A repo's design system exists
when either:

- `design/LOCK.json` is present, or
- `.github/bureau/design-system.json` names at least one folder that is present
  in the checkout.

If `design/LOCK.json` is present and there is no declaration, the folders are
`design/atoms`, `design/molecules`, `design/organisms` and `design/templates`.
The declaration's shape, what counts as a part, and a part's "ships at" path
are the contract in
[`docs/design-system-declaration.md`](../docs/design-system-declaration.md),
and `scripts/design_parts.py` is its one reader.

**Only a pull request's diff is judged.** Copies already in a repo go on
cleanup cards, filed by the one-time count (rule 8) and the weekly sweep, and
never on every pull request. A pull request is not held over a copy it did not
add or change.

### Uses the shared parts, and no copies (rules 2 and 3)

**Uses the shared parts.** New or changed UI composes from the shared atoms,
molecules, organisms and templates, and every value is a token.

**No copies.** A pull request that declares a second implementation of an
existing part, under any name, fails.

Both are **blocking REQUEST_CHANGES findings**, the same grade as a missing
test. Each finding names the part to use and the path it ships at, so the fix
agent can repair it unattended. A copy:

```
uses-the-shared-parts: `<Name>` in `<file>` is a copy of the shared part `<Part>` — use `<ships at>` and delete the copy.
```

A look-alike, the same structure, props and behavior under another name (the
critic's half of rule 9):

```
uses-the-shared-parts: `<Name>` in `<file>` looks like the shared part `<Part>` (shared props: <a, b, c>) — use `<ships at>` and delete the copy.
```

**A copy is deleted, not deprecated.** A deprecated copy is still a second
copy, and the next screen imports it.

The critic reads these findings off the parts review,
`python3 <pipeline>/scripts/parts_review.py review --diff <unified diff file> [--repo-root DIR]`.
It reads every component the diff adds or changes, and its props, from the diff
alone. It reads only the catalog from the checkout, and judges against the
catalog before the diff: the checkout's catalog with every part the diff adds
taken out. Its four kinds of finding are `copy`, `look-alike`, `new-part` and
`local-component`.

### A new part not in the system (rule 4)

The critic decides, in this order:

- **(a) An existing part fits.** If an existing part or a variant of one fits,
  the critic sends the work back with that part named. A new part that looks
  like a shared part goes back under 4(a): a `new-part` whose props share at
  least 2 names with a part in the catalog before the diff, at a Jaccard
  similarity of 0.6 or more, names the most similar such part. It is worded as
  the look-alike finding above, with `<file>` the new part's own file.
- **(b) It is genuinely new and well built.** Built from tokens, with every
  state, accessible and consistent with the system: the critic approves it.
  The part joins the design system as **provisional**, usable by every screen
  so nobody builds a second copy. Nothing waits on the CEO.
- **(c) The critic is unsure, or the part changes the overall look or the
  brand.** Only then does it raise one Green Light question, below.

The parts review words a new part with no near part:

```
new-part: `<Name>` at `<path>` — no shared part is near it; approve it as provisional with its `🪴 part-provisional:` line unless rule 4(c) applies.
```

### The provisional record (rule 5)

The critic cannot commit to the pull request, so its approval is a marked line
in its own APPROVE verdict, opening its own line:

```
🪴 part-provisional: `<Name>` at `<path>` — reasoning: <one sentence>
```

That line is the one record of a critic's approval. The gate, the weekly sweep
and the repo's own record (`LOCK.json`, or Portico's `atoms.approved.test.ts`)
read it, the way the merge gate reads `🔎 proof-row-accepted:`
(`standards/card-quality.md`). `parts_review.provisional_records(text)` returns
`(name, path, reasoning)` for every such line. The record is readable from
files and comments, never from a person's memory.

### The one question (rule 4(c))

When rule 4(c) applies, the critic's REQUEST_CHANGES verdict carries this
block, one line each, in this order:

```
🎨 design-question: `<Name>` at `<path>`
🔎 Finding: <one line>
❓ Question: <one line>
💡 Recommendation: <one line>
```

It is the one Green Light question, in the house Finding, Question,
Recommendation shape (`console_escalation.FINDING_PREFIX`, `QUESTION_PREFIX`
and `RECOMMENDATION_PREFIX`), and it carries the critic's recommendation. The
pipeline posts it to the card as one comment opening with the block's first
line. A comment opening with `🎨 design-question:` counts as the question only
when the pipeline posted it.

**The CEO's answer.** His signed console answer to the question settles rule
4(c) for that part on that pull request. The only answer that counts is a
comment `spoken_thread` labels `ceo-via-console`, one whose console signature
verifies (`standards/untrusted-content.md`). Once he answers, the pipeline
dispatches one re-review of the card's open pull request and posts the
hand-back record:

```
🎨 design-question-answered: `<Name>` at `<path>` — re-review of PR #<n> asked at <sha8>
```

The critic reads his answer in its context, as the `DESIGN ANSWER:` lines
`scripts/design_answer.py context <CARD>` prints, and writes the
`🪴 part-provisional:` line, citing his answer, or names the part he chose.

The question is asked once per part on a pull request. A question raised again
before he answers is parked without a second ask, and the critic never raises
it again once his answer has reached it. `design_answer.py state` reads where a
part stands: `none`, `asked`, `answered`, `handed-back` or `unknown`.

### The parts-check line

Every critic verdict on a pull request that changes a UI file in a repo whose
design system exists carries exactly one of these lines, so a miss is always on
the record:

```
Parts check: applied — <n> finding(s)
Parts check: applied by hand — the review read UNKNOWN (<reason>), <n> finding(s)
Parts check: not applied — <reason>
```

- `applied` where the review line says `applies`.
- `applied by hand` where it says `UNKNOWN` and the critic applied the
  checklist by reading the diff itself.
- `not applied` where it says `UNKNOWN` and the catalog block says `UNKNOWN`
  too.

A verdict where the review line says `does not apply` carries no such line.
Neither does a verdict on a pull request that changes no UI file.

### Builders get the parts list up front (rule 7)

Every agent that builds UI receives, in its context, the project's catalog of
existing parts with the file each one ships at. The catalog is computed from
the repo's files at run time by
`python3 .bureau-pipeline/scripts/design_parts.py catalog`, never kept by hand,
and in a repo with no design system it reads `PARTS CATALOG: none — <reason>`.

Three roles' context carries it: `engineer`, `frontend` and the fix agent. The
fix agent is one of them because it writes UI code when it answers a
REQUEST_CHANGES, and a fix for one finding can add a copy while it repairs
another.

A builder picks from the catalog first, and builds a new part only when nothing
in the catalog, or a variant of it, fits.

### Count before enforcing (rule 8)

Before the CI check gates anything in a repo, a one-time count runs there. Each
existing copy it finds becomes a cleanup card in Intake. The pull request check
covers only the diff, so nothing fails on old code on day one.

### Renamed copies are caught two ways (rule 9)

The mechanical check in CI flags a UI file the pull request adds, or in which
it declares a component, that imports none of the shared parts. The critic looks for look-alike parts: the same structure,
props and behavior under a different name, such as a `StatusBanner` that is
really a `Notice`. It sends one back with the look-alike finding above.

### The weekly list is visual (rule 10)

Each new provisional part the critic approved is shown on the CEO's weekly list
as a screenshot beside the closest existing part, with the critic's reasoning
and a way to reject it, so he approves or rejects by looking. Silence keeps it.

### Provisional becomes permanent after two weeks (rule 11)

A provisional part with no rejection becomes permanent two weeks after its
approval. The change is recorded automatically, with the date, in the same
record that holds the provisional approval (rule 5). A rejection inside the two
weeks removes the part from the system and files a card to replace its uses.

### The rollout order

The rollout goes agent-bureau's console, then Portico, then the other repos with
a design system. Each repo gets its one-time count (rule 8) before its CI stub
gates anything.
