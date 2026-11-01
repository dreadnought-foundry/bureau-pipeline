# A PROOF card open more than seven days, and what it waits on (DRE-8625)

A PROOF card that has been open more than seven days is read into one of five
waits: a release, a date, a press, a deploy, or an observation that cannot
recur. Each wait takes one machine action. The one reader of these rules is
`scripts/proof_overdue.py`. It reads and writes nothing back: no comment, no
hold, no lane and no label.

This is the epic's one docs page. The daily pass
(`scripts/proof_overdue_sweep.py`) and the action step
(`scripts/proof_overdue_act.py`) read this module's answer. Each states its
own rules in its module docstring and does not edit this page.

## Why

Since 2026-10-01, 59 of the 72 cards that waited more than 24 hours in
`In Review` were proof records. On 2026-11-01 DRE-6673 (In Review), DRE-6008
(Hand-work) and DRE-5357 (Hand-work) still stood. Each carried
`🔬 proof-waiting` holds and `🧱 proof-record-stuck` notes. None of them said,
in one word from a fixed list, what the proof was waiting on, so nothing could
take the action that would move it.

## The age clock

A proof is overdue when it has been open more than seven days (`AGE_DAYS`).

The clock starts at whichever came first:

- the card's first `🔬 proof-run` receipt;
- the `createdAt` of its open record pull request.

A card with neither is clocked from when it entered the lane it is in now. That
is the newest move into that lane on the card's history. The age is printed to
one decimal.

## The five classes and their actions

| Class | What the proof waits on | Action |
| -- | -- | -- |
| `release` | A release not yet out, or a named card (`DRE-n`) that is not Done yet and that a row, a hold or an open `blocks` relation waits on | `rerun-on-release` |
| `date` | A waiting row stamped `not-before=<T>`, or a time-only hold with no waiting row behind it | `rerun-on-date` |
| `press` | A press from the sandbox list in `standards/card-quality.md`, on the sandbox, on a PROOF labeled `repo:agent-bureau` | `sandbox-press` |
| `deploy` | A row or hold whose event names a deploy: a deployment record, a deploy run, a service rollout | `rerun-on-deploy` |
| `unrepeatable` | An observation that cannot recur unattended | `split` |

An observation that cannot recur is any of these:

- a history row (`Not met. history:`);
- a `state-gone` cause row;
- a hold that names a closed window or a finished run;
- any press that is not `press` above. That covers a press off the list
  (such as setting a project's Builders model, DRE-6638), a press off the
  sandbox, a press on a PROOF for another repo, and the CEO's own press.

Each diagnosis also says what the card waits on:

- the card id or release tag for `release`;
- the UTC time for `date`;
- the mutation name, such as `createTask`, for `press`;
- the deploy named for `deploy`;
- the criterion cut to 70 characters for `unrepeatable`.

## Precedence

A card's holds and rows can read more than one class. When they do, the card
gets the class that comes first in this order, the order of `CLASSES`:

`release`, then `date`, then `press`, then `deploy`, then `unrepeatable`.

When several rows read `date`, the card waits for the latest of their times.

## The read-through

The pipeline posts its own time-only holds:

- one to re-check a record's waiting rows twelve hours on (DRE-7951,
  `RECHECK_REASON`);
- one to time a re-run (`TIMED_REASON`).

These timers are not what the proof waits on. The rows they re-check are. So a
time-only hold reads `date` only when the record has no waiting row behind it,
for example a card's own `Observe after:` hold before its first run. Otherwise
the hold is read through, and the rows decide. A waiting row that carries its
own date still reads `date`.

## The elimination rule

Sometimes an overdue card reads none of the five classes. Its holds may wait on
an operator's reading, or its rows may plainly fail. That card reads
`unrepeatable`, marked as found by elimination: something is holding it, and it
is nothing the pipeline will wait out.

## What is read

- The card's open `🔬 proof-waiting` holds, read as the merge gate and the
  proof dispatcher read them.
- The unmet rows of its record. These come from its open record pull request at
  the head, or from its record branch at the head when no pull request is open.
- For a card never run, the release reading of its siblings' merges.
- The state of every card its holds and rows name, read in one request.
- Its `blocks` relations. A card blocking it that is not closed holds its first
  run (the dispatcher's condition 3) without posting a hold, so it reads
  `release`, naming that card. On 2026-11-01 three bureau-pipeline proofs waited
  in Queued this way, DRE-7293, DRE-7622 and DRE-7979, each on an operator card
  not yet Done.

A record pull request that changes an existing record file under `docs/` or
`architecture/`, instead of adding one, is still read here (DRE-5357's #3876).
The gate still refuses that shape.

## The replay

    python3 scripts/proof_overdue.py replay --repo <slug>
    python3 scripts/proof_overdue.py replay --fixture <path>

The first form reads a repo's open PROOF cards in every open lane, read-only.
The second replays one saved card, so the board of 2026-11-01 can be read again
after it has moved. Either form prints one line per overdue card:

    proof-overdue: DRE-<n> · <age> days · class=<class> · action=<action> · waits-on=<waits_on>

When no card is overdue it prints `proof-overdue: none overdue in <slug>`
instead. It exits 0 when the read completes and 1 when a card could not be
read. The card that could not be read is named on its own line and never
skipped.

The three cards of 2026-11-01 are saved under `tests/fixtures/proof_overdue/`.
They read as follows:

- DRE-6673 reads `date`. Its record's rows wait for the third seven-day window
  to close, at `2026-11-02T02:25:00+00:00`.
- DRE-6008 reads `deploy`. Observation 4 waits for a production
  `make deploy-console` beside a sleeping train run.
- DRE-5357 reads `deploy`. Its record now waits on DRE-8154 shipping and
  AgentLogStack deployed, and DRE-8154 is already Done.
