# Design-parity standard — cards must sum to the design

`standards/design.md` makes one card faithful to one screen. This standard
closes the gap ABOVE it: the set of cards must add up to the whole design.
(Origin: the DeltaSolv gap audit, 2026-07-13 — every card built and verified
as written, yet ~67 designed screens were never carded, 5 Done cards had
unmet acceptance criteria in shipped code, and load-bearing surfaces were
facades: no web login, media never uploaded, fake transcription. Nothing in
the loop checked that the cards summed to the design.)

## Planner obligation — account for EVERY designed surface
When an epic references a design contract — a `design/` directory,
`**Design:**` refs, a screen inventory/MANIFEST — enumerate every designed
surface in scope BEFORE cutting cards. The plan must account for each one,
exactly two ways:
- **A card** carrying a `**Design:**` ref that names that surface, or
- **An explicit deferral** in the plan comment, one line per surface:

      deferred: <surface> — <reason>

  The reason is mandatory — a reason-less deferral is a silent omission
  dressed up. Deferring is a decision the CEO can read and veto; omitting is
  a planning defect.

Silent omission is a planning defect. The mechanical form of this check is
`scripts/design_parity.py` (`unaccounted_surfaces`): a surface is accounted
for ONLY by a card's `**Design:**` ref (matched by screen filename) or a
deferred line with a reason — prose mentions do not count.

## An approved mockup — every inventory row, one to one (DRE-8612)
A screen-level match is not enough when the epic is built from an approved
mockup. On 2026-10-19 the Initiatives screens shipped without their chevrons:
the screen was carded, the chevron was a detail inside it, and nobody checked
the cards row by row against the mockup's committed inventory.

- **The trigger.** The epic's description, the plan artifact, or an open
  child's body names an approved mockup: a path under a design folder's
  `approved/` directory (`console/design/approved/…` or `design/approved/…`)
  other than a file ending `-inventory.md`, or an `.html` file inside a folder
  named `mockups`. A screen PNG under `console/design/images/screens/` is not
  one — the surface rule above covers those.
- **The `**Inventory:**` line.** The plan artifact names each committed
  inventory on a line opening `**Inventory:**`, as a repo-relative path —
  several comma-separated, or one line each. The inventory is read from the
  plan run's own checkout, never another repo; one that is absent, unreadable
  or holds no table rows is a send-back that names it, never a pass. A row is
  every body row of every markdown table in the file, header and separator
  rows excluded, and its key is its cells, trimmed, joined with ` | `.
- **The `inventory-coverage` block.** `## The cards` carries a fenced block
  with that info-string: a JSON list with one record per row, each naming its
  `inventory`, its `row` key, and either a `card` and that card's `criterion`
  or a `deferred` reason.
- **What covers a row.** A record naming the row's key and either an open
  child of the epic with one of its `- [ ]` criteria, word for word with
  whitespace collapsed, or a non-empty `deferred` reason — the `deferred:
  <surface> — <reason>` rule above at row level. A screen-level match never
  covers a row.

`scripts/plan_inventory.py` enforces it: each defect is an `inventory:` line
in the structural file the seam gate forwards into the first critic's result,
so the round is sent back whatever the critic wrote. The row-to-criterion
pairs go on the epic's mechanical note; whether a criterion really delivers
its row is the critic's judgment, not the script's.

## Card obligation — observable acceptance criteria
- Every UI card carries a `**Design:**` ref to the specific screen/mock
  (see `standards/design.md` for the path convention).
- Acceptance criteria must be observable in the RUNNING product. "Renders
  from live data" is NOT satisfied by a static empty state; "user can log
  in" is NOT satisfied by a login form that posts nowhere.

## What the fleet can actually check, and what it cannot
**Static visual fidelity is fleet-checkable. Interactive behaviour is not.**
`qa-review.yml` runs a visual-QA stage: it installs chromium via Playwright,
screenshots the changed screens, and hands the critic both the design PNG and
the render with the instruction to compare them. That is why a card whose
acceptance criteria say the screen "renders" its content routes FLEET.
Screenshotting a screen is not driving a flow, and a flow the fleet cannot
drive is not a reason to hold the build (DRE-6227): a criterion that says "sign
in", "verified live" or "in production" can only be met by watching the change
run after it ships, so it is written as a proof observation on the epic's
`PROOF:` card, or as a follow-up card under DRE-3075's two-cards shape
(`standards/card-quality.md`). The build card stays FLEET.

**State the rule; do not promise it always decides.** DRE-2831 found the
mechanical FLEET signal firing on almost nothing: it matched phrases nobody
writes — `pixel-perfect`, `visual parity`, `matches the design`, none of them
in a single one of this workspace's 1,561 carded issues — so genuinely visual
cards returned nothing and fell through to a model. The phrases are now read
off real cards (`renders`, `rendered`, `design tokens`), and on the same
corpus the signal decides 50 of the 86 carded UI cards instead of 3.

**The other 36 still fall through, and that is the honest half.** A criterion
like *"File on `main`, byte-identical to `origin/ui/workbench`"* names no
rendered outcome, and nothing here pretends a machine can route it. So a FLEET
verdict on a visual card does not always mean the mechanical rule decided it,
and it never means a machine compared two images. When you need the comparison
to have happened, look for it in the critic's verdict rather than assuming the
stage decided.

**A stage that could not render now says so on the verdict (DRE-3248).** It
used to tell the critic a failed render was infrastructure flakiness and to
raise nothing, so a gate that never ran read exactly like a gate that passed —
five deliberately-broken screens produced no visual finding at all. A degraded
stage still does not block a merge; it now puts one plain line on the verdict,
`Visual check did not run: <reason>`. The absence of that line is what tells
you the comparison actually happened.

## Critic/verifier lens — shipped surface vs design ref (blocking)
For any `**Design:**`-bearing PR, compare the shipped surface against the
design ref AND the card's acceptance criteria:
- A control present in the design and absent in the diff is a **blocking
  finding**, unless the card explicitly descoped it.
- **No fake states**: a spinner must resolve to real data, a button must
  navigate or act, and "AI-suggested" content must not be hardcoded. A
  surface that demos well but does nothing is a facade — block it.

## Epic-close gate — deferred ≠ forgotten
An epic whose plan deferred surfaces closes WITH a ledger comment listing
every deferred surface and its reason (recover them with
`design_parity.deferred_surfaces` over the plan comment). A deferral with no
ledger at close is how DeltaSolv lost 67 screens — the ledger is what turns
"deferred" into follow-up work instead of a forgotten gap.
