# CEO's-press holds open on cards outside Green Light — 2026-10-20 (DRE-7741)

The one-time check DRE-7741 asks for: every `🔬 proof-waiting` hold naming
"the CEO's press" that is still open on a `PROOF:` card outside `Green Light`.
Only his signed console answer discharges such a hold, and the return branch
that waits for that answer reads `Green Light` only. Before DRE-7741 the
sweep's held-proof pass called these holds "the return branch's" and said
nothing.

## How it was read

Read at 2026-10-20 12:15 PT (19:15:29 UTC) with the pipeline's Linear key:

- every open card on the board whose title opens `PROOF:` — 59 cards, any
  team and repo, in a state Linear does not type `completed` or `canceled`;
- each card's whole thread (`linear_ops._thread_and_viewer`, `whole=True`),
  read by `spoken_thread.voices` with the key's viewer;
- the open holds over those voices by `proof_record.open_holds_at` — the same
  reader the dispatcher and the PROOF close use — keeping each hold whose line
  names `proof_record.CEO_PRESS`.

Lanes read: Queued 27, In Review 22, Intake 5, Hand-work 3, Duplicate 2. No
`PROOF:` card was in `Green Light`. No console answer came back unchecked.

## What it found

Two holds are open, both on `repo:agent-bureau` cards In Review, both written
by the proof run on 2026-10-10 PT:

| Card | Lane | Hold posted | Hold, first line (cut) |
|---|---|---|---|
| DRE-6663 (#3589) | In Review | 2026-10-10 20:09 PT | `🔬 proof-waiting: needs the CEO's press: on the open groom proposal on the standing card, press Not doing on one card, choose a reason from the list, and save; …` |
| DRE-6694 (#3603) | In Review | 2026-10-10 22:00 PT | `🔬 proof-waiting: the criterion the question below names — needs the CEO's press: 🔎 Finding: 1 row of the proof record cannot be observed as worded (cause: out-of-reach) …` |

Neither thread carries a signed console answer after its hold. The comments
after it — an operator's `🔬 proof-observed` on DRE-6663 and on DRE-6694, an
operator's answer on DRE-6694, and an operator-decision note on DRE-6663 — do
not discharge a hold naming the CEO's press (`proof_record.open_holds`).

## What the change does about them

Run against both live threads with the write stubbed, the new
`reconcile.alarm_held_proofs` reads each as held for the operator under its
CEO's-press hold, about 230 hours past the 24-hour bound, and would post one
`🚨 hand-work-overdue` alarm on each, naming the card, the lane and the hold:

> 🚨 hand-work-overdue: DRE-6663 has waited In Review for the operator 232
> hours, past its 24 — the operator's to look at.
> Kind: a hold naming the CEO's press on a card in In Review, not Green Light
> — only his signed console answer lifts it, and the return branch reads Green
> Light only: get his answer, or re-sort the hold — 🔬 proof-waiting: …

agent-bureau's sweep rides `@main`, so the alarms post on its first full pass
after this change merges. The operator gets his answer or re-sorts each hold.
Nothing in this change moves either card or writes to either thread.
