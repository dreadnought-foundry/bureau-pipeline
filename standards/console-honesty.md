# Console-honesty standard — badges derive from what actually happened

Applies to ANY console/UI surface that renders pipeline state: badges,
buttons, status chips, lane counts, run indicators, alert rows. The console
is how the CEO reads the system — a state element that guesses is worse than
no element at all, because a confident wrong answer triggers wrong action.

Origin (DRE-2023, 2026-07-10): three builds ran ~45 minutes with real
progress receipts, but the watchdog inferred death from an adjacent signal
(In-Progress-with-no-PR staleness) instead of asking GitHub whether the run
was actually alive. It requeued the "dead" cards, the fresh dispatch
cancelled the live runs via the per-card concurrency group, and the dead-run
cap parked them needs-human — the inference caused all three deaths it was
counting. Any surface that renders state it inferred rather than fetched
fails the same way.

## The three rules

1. **Derive from truth, never infer from adjacent signals.** Every badge,
   button, and status derives from what ACTUALLY happened — live run state
   fetched from the source of truth (the GitHub run's own status, the PR's
   own merge state, the card's own Linear state), never inference from
   adjacent signals (elapsed time, a sibling event, the absence of a PR).
   A critic crash renders as "review didn't run", never as "critic rejected"
   — a crash and a rejection are different facts with different next
   actions, and only the source of truth can tell them apart.
2. **Define stale-data and absent-data rendering explicitly.** Every state
   element states what it shows when its data is stale and when its data is
   absent. Unknown is shown as unknown — never as the last known value
   (a frozen "running" badge over a dead run is a lie), and never as a
   ghost row (an entry rendered from a record whose backing state no longer
   exists). "The query returned nothing" and "the thing is in state X" get
   visibly different renderings.
3. **Every state element ships with a stale/absent-data test.** The test
   feeds the element stale and missing data and asserts the unknown/absent
   rendering from rule 2 — it must FAIL if the element falls back to a
   last-known value or invents a state. A console card without one is
   incomplete: the happy-path render proves nothing about the failure modes
   this standard exists for.

## Critic: the three rules are a review gate

On a console card — any PR adding or changing a surface that renders
pipeline state — the critic explicitly checks the three rules above: does
each element fetch from the source of truth (rule 1), does it define its
stale/absent rendering (rule 2), and does the stale/absent-data test exist
and actually exercise those paths (rule 3)? A missing answer is a
REQUEST_CHANGES-grade finding, same as a missing test — cite the rule by
number and say what evidence would satisfy it.
