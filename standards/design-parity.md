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
acceptance criteria say the screen "renders" its content routes FLEET, while
one whose criteria say "sign in", "verified live" or "in production" routes
WORKBENCH — screenshotting a screen is not driving a flow.

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
