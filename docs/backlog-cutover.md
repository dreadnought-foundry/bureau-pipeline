# The cutover — the Backlog moves to Intake, and nobody is exempt

`scripts/backlog_cutover.py`, run by hand by the operator. DRE-2687.

Every Backlog card was created before the front door existed, and none of them
carries a routing verdict. They are not work — they are a list of things
somebody once wanted. They move to `Intake` and are re-planned before they are
work again.

This is the mechanism. The policy — the dated line that says everything before
it is legacy — is DRE-2728's ADR.

## There is no allowlist

The 2026-08-23 decision (D4) exempted four classes from the move: cards inside
`promote_ready()`'s reach, `needs-human` cards, operator / `no-code` cards, and
the paused DeltaSolv set. The CEO withdrew it on 2026-08-26, and the reason is
mechanical rather than a change of mind.

DRE-2725's guard says a card with no verdict cannot rest in any lane, and an
exempt card is precisely a card with no verdict sitting in Backlog. **The guard
would have bounced all of them to Intake on its first sweep whatever this script
said.** There were only ever two consistent positions: exempt the legacy cards
from the guard permanently — which is the two-population board DRE-2728 exists
to prevent — or move everything.

The exemption is replaced by an **ordering**, not removed and forgotten:

1. **The promoter-reach cards go first.** They were exempted because they are
   live work and stranding them costs real time. That concern is right and
   sequencing is the fix: stranded for hours, not parked forever, and no second
   population is created to achieve it. Reach is computed from live state — the
   epic's own lane, the card's own verdict — never from a list of ids.
2. **Then newest-first through the rest** (D2, approved 2026-08-23). A wrong
   verdict on a card the operator still remembers is spotted instantly; a
   two-month-old card gives nobody that. The stated cost is accepted: the newest
   cards are also the best-formed, so the first batch flatters the classifier
   and the hard cases are met last.

## Grandfathering is not an exemption

A card with an **open pull request**, or a **run receipt less than an hour old**,
is justified in its lane by evidence and finishes under the old rules. The
distinction is drawn on that evidence and never on a list of ids —
`card_ids_in_code()` fails the build if an id ever appears in this script's code.

An in-flight card that comes *back* for rework goes to Intake like anything
else, because that is where rework re-enters.

If GitHub cannot be read, the run refuses. An unreadable answer is not "no pull
request" (DRE-2034), and acting on one would yank a card out from under a live
build.

## Running it

```
python3 scripts/backlog_cutover.py census                 # what the lanes hold
python3 scripts/backlog_cutover.py plan                   # the ordered list
python3 scripts/backlog_cutover.py plan --only DRE-N      # just these cards
python3 scripts/backlog_cutover.py run                    # dry run, writes nothing
python3 scripts/backlog_cutover.py run --apply --record DRE-N
python3 scripts/backlog_cutover.py run --apply --only DRE-N --record DRE-M
```

`run` writes nothing without `--apply`. `--limit N` bounds a single run, in
order, so the first pass can be a handful of cards read by eye before the rest
follow. `--record` posts the occupancy record — every lane's count immediately
before and immediately after, which cards were batch one, and which were left
alone with the evidence that justified it — to the card named.

## Rehearsing it on one card

`--limit N` bounds a run but cannot **name** a card: it takes whichever real
cards the plan puts first, so a throwaway probe dropped into Backlog could not
be moved alone and nobody could watch a card take this path before the real run
(DRE-3013's finding 4).

`--only DRE-N [DRE-M …]` restricts the population to the cards it names, on
`plan` and on `run`. Everything else is unchanged and deliberately so — the
in-flight test still holds a named card back and still says why, the reason is
still posted *before* the move, and the move is still guarded on the lane it was
read in. A named card that is **not in Backlog** is reported as such and
skipped; the run never reaches into whatever lane it is actually in. A value
that is not a card identifier stops the run before it reads anything, because
"not in Backlog" is a claim about the board and a typo must not make one.

The occupancy record for an `--only` run **is not the cutover's record and
cannot be read as one**: it opens by saying a rehearsal ran, names the cards it
ran on, and states that the cutover has not run. Ask whether the cutover has
happened after a rehearsal and the answer is still no.

```
python3 scripts/backlog_cutover.py run --apply --only DRE-<probe> --record DRE-3013
```

Each moved card carries a comment saying, in the CEO's language, why it moved
and what happens next. The comment is posted *before* the move, so a move that
fails still leaves the reason on the card.

There is no schedule and no workflow button. This is a one-time cutover run by a
person who is watching it.

## Backlog is empty on cutover day

Not nearly empty. Empty. It refills only with verdict-carrying cards at the rate
Planning produces them. **Expect the board to look alarming for about a week**,
and say so in advance rather than explaining it afterwards. The occupancy record
is what a later "the board looks wrong" gets compared against.

## What drains Intake afterwards

The groomer (`docs/groomer.md`) sequences Intake into batches the CEO approves,
and the approved batch goes to Planning.

And if nobody runs it: an Intake card that sits past the lane contract's stall
window for `Intake` — 48 hours — is **moved** by the sweep to `Green Light`,
carrying whatever reason is already stated on it
(`reconcile.escalate_aged_intake`). Not reported: moved. About 480 consecutive
green sweeps once printed, in plain English, the exact reason five cards were
frozen, and nobody read one. A report is a record; a move is a gate.

The escalation is capped at three cards per sweep so that a 220-card Intake
cannot empty itself into the CEO's queue in one go. The cap holds the remainder
— they stay in Intake, still the oldest, and the next sweep takes the next
three. It may hold a card; it may not forget one.

Two cards never age out at all: one labelled `hand-built` (no classification is
coming from the pipeline for work a person is doing by hand) and one carrying
the **PARKED** routing verdict — the vocabulary's own "deliberately not
dispatchable, never reported as stalled". A clock that moved a PARKED card into
Green Light would un-park a decision somebody made on purpose, in the loudest
place available.

## Controlling the inflow — the pen the operator holds

Three things control how fast work enters the pipeline after the cutover, and
between them the inflow is exactly the batches the CEO approves, at the capacity
he sets, and nothing else:

1. **The groomer batch** — the valve. Nothing leaves Intake without the CEO
   approving that exact batch (`docs/groomer.md`).
2. **PARKED** — the per-card "stay still", described above.
3. **The sweep's three inputs**, below. They are **`workflow_call` inputs on the
   repo's `reconcile.yml` stub** — set them there, in the file, where the repo's
   other per-repo values live and where `make check-channel-fleet` reads them.
   There is no env var to edit and no pipeline release to cut.

| Input | What it does | Empty means |
| -- | -- | -- |
| `intake_hold` | **The switch.** Set it — ideally to the date you set it — and the age-out moves nothing and the groomer's `drain` refuses. Each prints one line per pass: *"Intake held by the operator since &lt;date&gt;; N cards waiting, M past the window"*. The pen is visibly closed, not silently stuck. | open |
| `intake_max_age_minutes` | How long a card may sit in Intake before the sweep escalates it. | the lane contract's own 48-hour window |
| `intake_escalation_cap` | How many aged cards **one sweep** may move. | three |

`intake_hold` belongs on **both** stubs — `reconcile.yml` and `groomer.yml` —
because the age-out and the drain are the two things that move a card out of
Intake. It is stub data rather than a dispatch input on purpose: a hold the
person running the drain can waive is not a hold.

**Plan the cutover and the first groomer batch together.** Every card moved on
cutover day gets 48 hours of grace and then starts trickling into Green Light —
which is the pressure working as designed, and it is still pressure the CEO
feels. **Set `intake_hold` to the cutover date before the run and clear it when
the front door is proven**; if the drain will genuinely take longer than the
window, widen `intake_max_age_minutes` for the cutover instead of letting the
queue fill. Either is a deliberate operator act with an end date, not an edit to
the rule.

```yaml
# .github/workflows/reconcile.yml in the product repo — the pen, held
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@stable
    with:
      pipeline_ref: stable
      intake_hold: "2026-09-08"       # clear this when the front door is proven
      intake_max_age_minutes: "20160" # 14 days, for the cutover window only
    secrets: inherit
```
