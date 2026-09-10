# Plan-artifact standard — what an epic produces for the CEO to green-light

An epic's CEO-facing output is a published document, not a Linear comment. A
comment cannot hold a diagram, a mockup, or anything navigable, and its KPI
section is prose — which is the failure this standard exists to stop.

The planner writes one markdown file; the plan run turns it into `plan.html`
and publishes it. The planner needs no new tool for this: it writes markdown
with `Write`, and the artifact is an **output of the run**, not a capability
the agent holds. `scripts/plan_artifact.py` is the mechanical form of
everything below — `check` is the gate a plan passes before it reaches the
CEO.

## The seven sections

Every artifact carries all seven, as `##` headings. A trailing clause is fine
(`## KPIs — as structured data`); an unrecognised heading reads as a missing
section, not a substitute.

| Section | Answers |
| -- | -- |
| **Business case** | Why this, why now, what it is worth |
| **KPIs** | Which numbers move, from what baseline, in which direction |
| **Risk assessment** | What can go wrong, blast radius, reversibility |
| **Outcome** | If this goes right, what is different for the business |
| **Visual model** | For UI work: the screens, built from `console/design/tokens.css` |
| **The cards** | The decomposition, in dependency order, collisions named, each child's ledger check recorded |
| **Proof and demo** | How we will know it works, and how the CEO will be shown |

**Proof and demo is a section AND two cards (DRE-2746).** This section says how
the epic will be proven and shown; the epic's last two children — a `PROOF: …`
card and a `DEMO: …` card, blocked by every sibling and never `FLEET` — are
where it actually happens. The section is prose the planner writes; the cards
are checked on the planner's output, and an epic missing either is bounced back
to `Planning`. `standards/card-quality.md` carries the full rule.

## KPIs are data, not prose

This is the difference between trackable and decorative. The KPIs section
carries a fenced block with the info-string `kpis`, holding a JSON list:

    ```kpis
    [
      {"name": "Time to green light", "baseline": 4.0, "unit": "hours",
       "direction": "down", "target": 1.5},
      {"name": "Plans sent back for rework", "baseline": 6, "unit": "per week",
       "direction": "down", "target": 2}
    ]
    ```

Required fields: `name`, `baseline` (a NUMBER — "quite slow" is prose wearing
a field name), and `direction` (`up`, `down` or `flat`). Optional: `unit`,
`target`, and any note fields you want. Prose around the block is welcome —
say how the baseline was measured — but the block is what a close-out reads.

The reason is KPI objective O10, pushed down one level from the wave to the
epic (`standards/wave-plan.md` §6 — *"predicting two and moving two
is a result; moving two and then naming them is a story"*). As prose, "did it
move the number" becomes a memory exercise. As data,
`plan_artifact.py closeout` diffs prediction against outcome and reports three
things the CEO reads differently: KPIs that moved as predicted, KPIs predicted
and never measured, and KPIs measured but never predicted — the story case,
named mechanically instead of argued about afterwards.

## The ledger check is data, not prose

`## The cards` says how the epic was cut. It also says how each cut was
**sized**, as a fenced block with the info-string `ledger-check`, one JSON
record per child:

    ```ledger-check
    [
      {"card": "DRE-1234", "tells_checked": ["contracts-between-pieces"],
       "ledger_match": "DRE-3088", "ledger_status": "fresh"},
      {"card": "DRE-1235", "tells_checked": ["two-languages-or-tiers"],
       "ledger_match": "none", "ledger_status": "fresh"}
    ]
    ```

Four keys, all required:

- **`card`** — the child's id.
- **`tells_checked`** — which of DRE-2893's four tells the card was read
  against: `contracts-between-pieces`, `two-languages-or-tiers`,
  `unenumerated-count`, `unbounded-quantifier` (`standards/card-quality.md`
  carries what each one means). The list is `split_ledger.TELLS`, and a name
  outside it is a defect.
- **`ledger_match`** — the nearest split-ledger row, or the string `none` when
  no row is near. `none` is an answer; an empty field is not.
- **`ledger_status`** — `fresh`, or a string starting with `UNKNOWN`, copied
  off the `LEDGER STATUS:` line of the ledger block in the planner's context
  (`scripts/ledger_context.py`).

**`UNKNOWN` is a required value, not a failure.** When the ledger was missing,
unreadable, malformed or stale, every record carries that whole
`UNKNOWN — <reason>` status and the artifact still PASSES `check` —
`tells_checked` may then be empty, because there was no evidence to check
against. What does not pass is **silence**: an artifact with no `ledger-check`
block is a defect, because an omitted check reads as a check that passed, and
nobody reading the plan afterwards can tell the difference between "sized
against the ledger" and "never looked".

Every child needs a record and every record needs a child. When the run knows
the child ids it passes them (`plan_artifact.py check --children-file`), and a
child with no record fails by name — that omission is the exact thing this
block exists to make impossible.

## Visual model — the mockup IS the UI

For UI work the visual model is a fenced ```` ```mockup ```` block of HTML
built from `console/design/tokens.css` — `var(--…)` custom properties, not
hex codes. Then it is not a picture of the UI, it is the UI, it inherits the
design system by construction, and it is the spec the fleet builds against.

**A screenshot is not a visual model for a screen that does not exist yet.**
The old convention pointed at an exported PNG under
`console/design/images/screens/`; for a NEW UI there is no PNG, so the CEO
would be approving a text plan for a screen he cannot see. A visual model that
is only an image is a defect on its own evidence.

**"Live" means styled, never executing.** The mockup is the one place the
artifact publishes markup rather than escaped text, and the artifact is written
by an agent reading untrusted epic text — so the block passes through an
allowlist on the way to the page. Layout, text, tables, controls and inline SVG
survive; `<script>`, `<iframe>`, `<object>`, `<form>`, every `on…` handler,
`javascript:` URLs and `expression()`/`url()` in a `style` attribute do not.
Nothing a mockup needs is on that list — a mockup gets its look from
`tokens.css`, not from behaviour. The check reports what it removed rather than
stripping in silence, so a planner is never left believing the CEO is looking
at what it wrote, and the published page also carries a Content-Security-Policy
that forbids script execution outright.

A non-UI epic writes `Not applicable — <reason>` (the same stated-reason
grammar as `deferred: <surface> — <reason>` in `standards/design-parity.md`).
Silence is not a decision. An epic whose CARDS carry `**Design:**` refs is UI
work and cannot take that escape hatch — the run reads the refs off the cards,
not off the plan's own say-so.

## Built for revision, not approval

A plan arriving in Green Light gets substantial feedback and goes round again.
That is the normal path, not the exception, so the artifact carries:

- **A generated version record.** `## Version record` says what changed since
  the published previous version, and what did not — "where do I re-read" is
  the CEO's actual question. It is produced by diffing the published source
  against the new one, never written by hand, and each revision REPLACES the
  last one rather than stacking (revision three's record answers "since you
  last read it", so revision two's is history).
- **Anchors.** Every section and every paragraph in `plan.html` carries a
  stable id (`#outcome-p1`, `#visual-model-mockup`), numbered within its
  section so editing the business case does not move the anchor a comment on
  the outcome is bound to. Feedback lands on the paragraph or the mockup it is
  about, rather than as prose someone has to map back onto the document.

## Publishing

`plan.html` is a build output. The run renders it and publishes it to the
document portal at `plans/<epic>/` — a path derived from the epic id alone, so
revision two lands on top of revision one and **the link the CEO holds never
moves**. The page's source markdown is published beside it, which is what
makes the next version record generated rather than remembered.

Publishing goes through the **committed pipeline route**, never the portal's
"Add document" button: that button strips scripts, and an interactive mockup
uploaded that way renders looking complete while being dead.

With no portal configured there is no URL, and the run says so plainly — the
artifact is attached to the run instead. A guessed URL the CEO follows to a
404 is worse than none (`standards/console-honesty.md` rule 2).

## Critic: the artifact is a review gate

On a plan, the critic checks: all seven sections present; the KPI block
parses and every record carries `name`, a numeric `baseline`, and a
`direction`; the risk assessment names blast radius AND reversibility; a UI
epic carries a live token-built mockup rather than a screenshot; the cards are
in dependency order with collisions named; the `ledger-check` block records
every child, with an `UNKNOWN` status where the ledger was missing or stale
rather than no record at all; and proof-and-demo says how the CEO will be
shown. A missing answer is a send-back, same as a missing test — cite the
section and say what would satisfy it.
