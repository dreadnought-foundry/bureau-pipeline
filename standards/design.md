# Design standard — brand and UI fidelity

The rules for anything that produces a look: a brand from a business concept, a
UI screen, or a marketing asset. The craft bar is constant; the look is fresh
per concept.

## Brand from a concept (one source of truth)
- A brand is **derived, never one-off.** Intake a business concept into a
  **Brand Brief** (what it does · audience · positioning · 3–5 personality
  adjectives · references · name · constraints). If the concept omits a field,
  propose and confirm — never silently guess.
- Generate the **foundations once** as machine-readable **design tokens** (color,
  type scale, space, radius, elevation, motion). Every artifact — website,
  decks, social, app — inherits from the tokens. **If two assets disagree, the
  tokens win.**
- **One creative gate:** after the foundations are generated, the human picks one
  direction. Everything downstream is mechanical derivation from the locked
  tokens — coherent by construction.
- Deliver both: the `tokens` file (consumed by every artifact) AND the
  human-readable brand book documenting them.

## The quality bar (every brand inherits these)
1. **Coherent** — derive from the tokens; nothing one-off.
2. **Clean & intentional** — confident whitespace, no clutter.
3. **Accessible** — WCAG-AA contrast, legible type, responsive,
   keyboard-navigable. Non-negotiable.
4. **Restraint** — disciplined color (60/30/10) and motion (serves meaning,
   never decoration).
5. **Icons over words.** Linear is the north star: a compact progress ring
   ("5/13" closing a circle) beats "5 / 13 done · 8 left". When a label can be an
   icon + a number, make it one. Visual density over word density.

## The `**Design:**` card convention (LIVE)
- Every UI card carries a `**Design:**` line naming the EXACT exported screen
  PNG to build to (e.g. `console/design/images/screens/desktop/board.png`).
  Non-UI cards omit it — its absence is normal, never a defect.
- **Engineer:** Read that PNG (it's a normal-sized export) BEFORE building, and
  match it — layout, structure, components, spacing, copy. Divergence requires
  explicit justification in the PR description.
- **Never open the multi-MB `.pen` source** or other large binaries — `ls -la`
  first; anything over ~256 KB floods context and kills the run. Read the
  exported PNG / text extract instead.
- **Critic/Verifier:** the critic compares a rendered screenshot to that PNG and
  blocks on a material mismatch (failing OPEN — repos with no design images
  skip). "Unit tests green" does not mean "looks like the design."

## Stack note
A public/marketing site may use Astro (evaluate before Next.js); an
authenticated cockpit is always the Vite SPA. The website's section/component
library is built from the tokens. See `standards/architecture.md`.
