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

**And if nobody runs it, nothing moves.** That is the rule as of DRE-4141, on
the CEO's signed console answer of 2026-09-17: *"A card is never moved out of
Intake because it is old. No 48-hour age-out and no window of any length; it
stays where it is."* The groomer's approved batch is the only exit.

The sweep still LOOKS at the lane. Every full pass prints one line —
`intake-depth: N cards waiting in Intake, oldest D days` — in its own run log,
and that is the whole of what it does there. A report, never a move.

### What used to be here, and why it went

DRE-2687 gave Intake a timer: a card past the lane contract's stall window for
`Intake` — 48 hours — was **moved** by the sweep to `Green Light`, three per
sweep, carrying whatever reason was already stated on it. The argument was that
a report is a record and a move is a gate, and about 480 consecutive green
sweeps had once printed the exact reason five cards were frozen with nobody
reading one.

As a gate it fired on the whole lane:

* **2026-09-10** — it put about 130 cards into Green Light in one morning.
  `INTAKE_HOLD` was set on every repo to stop it and stayed set.
* **2026-09-16** — the CEO's signed answer on DRE-4078: the 48-hour age-out
  *"is what put 130 cards in my queue on 2026-09-10, and it stays switched off
  until a card fixes that separately."*
* **2026-09-17 05:33 PT** — one repo's hold was deleted for eight minutes by a
  session that believed it was a repo-local setting. It is not: an Intake card
  carries no `repo:` label, so **any one repo's sweep ages Intake for the whole
  fleet**. Three cards reached the CEO's queue, two of them belonging to a repo
  whose own hold was still set, and 221 more were past the window behind them.

A valve held shut by four hand-set variables, that floods the decision queue the
moment one of them is touched, is not a valve. The fear it answered — a lane
nothing drains — is answered instead by the groomer running on a schedule
(DRE-3586) and by the depth line above.

**Two retired inputs.** `intake_max_age_minutes` (the window) and
`intake_escalation_cap` (how many one sweep could move) are still DECLARED on
`reconcile.yml` and read by nothing: a `workflow_call` input a caller passes and
the reusable does not declare is a hard error, so they are accepted and ignored
until the stubs drop the lines. If your stub still passes either, delete it —
there is no window to widen and no cap to raise, because there is no move.

The **off-rail refusal** (DRE-3629) went with them. It fenced sandbox sweeps out
of the age-out after they helped age 130+ cards on 2026-09-09/10, and a sweep
that moves nothing needs no fence.

## Controlling the inflow — the pen the operator holds

Three things control how fast work enters the pipeline after the cutover, and
between them the inflow is exactly the batches the CEO approves, at the capacity
he sets, and nothing else:

1. **The groomer batch** — the valve, and since DRE-4141 the only exit from
   Intake. Nothing leaves without the CEO approving that exact batch
   (`docs/groomer.md`).
2. **PARKED** — the per-card "stay still". A PARKED card is deliberately not
   dispatchable and is never reported as stalled by any sweep.
3. **`intake_hold`** — the switch, below. It is a `workflow_call` input on the
   repo's **`groomer.yml`** stub, threaded from a **repository variable**
   (DRE-3285) rather than committed into the file, and `make
   check-channel-fleet` reads it there. There is no env var to edit and no
   pipeline release to cut.

| Control | What it does | Empty means |
| -- | -- | -- |
| `intake_hold` | **The switch.** Set it — ideally to the date you set it — and the groomer's `drain` refuses, printing one line per pass: *"Intake held by the operator since &lt;date&gt;; N cards waiting, …"*. The pen is visibly closed, not silently stuck. | open |

**`intake_hold` belongs on the `groomer.yml` stub, and only there** (DRE-4141).
It used to belong on `reconcile.yml` as well, because the age-out and the drain
were the two things that moved a card out of Intake and a switch only one of
them read was a pen with a hole in it. There is one of them now. It is never a
dispatch input: a hold the person running the drain can waive is not a hold.

**It comes from a repository variable, not from the file (DRE-3285).** The stub
passes `intake_hold: ${{ vars.INTAKE_HOLD }}`, so an unset variable renders
empty and the pen is open — the fleet's normal state. Closing and re-opening it
is one command per repo:

```
gh variable set INTAKE_HOLD --repo <owner>/<repo> --body 2026-09-08
gh variable delete INTAKE_HOLD --repo <owner>/<repo>
```

A value committed into the stub is reachable too, and only through a pull
request, a critic round and the merge gate — on the one day somebody needs the
pen shut within the hour, and per repo, as each queue drains at its own pace.
`make check-channel-fleet` in agent-bureau lists which stubs pass the variable;
one it reports as outstanding cannot be held without a PR.

**Plan the cutover and the first groomer batch together.** Cards moved on
cutover day sit in Intake until the groomer proposes them and the CEO approves
the batch — no grace period, because there is no clock. The pressure is the
DEPTH of the lane, which the sweep reports and the console shows, rather than a
queue filling itself. **`INTAKE_HOLD` is still worth setting on cutover day** if
the first batches should wait, and clearing it repo by repo as the groomer
catches up with each queue.

```yaml
# .github/workflows/groomer.yml in the product repo — the pen, wired
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/groomer.yml@stable
    with:
      pipeline_ref: stable
      intake_hold: ${{ vars.INTAKE_HOLD }} # unset = open; set it to hold
    secrets: inherit
```
