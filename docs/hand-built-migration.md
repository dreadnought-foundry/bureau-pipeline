# The hand-built migration

A one-time pass, run by hand, that takes every automatically applied
`hand-built` off the open cards, once (DRE-6230, epic DRE-6219).

The CEO's rule of 2026-10-07: "Build it and let everyone know when i say built
it, I'm saying this is a hand built card." The `hand-built` label is his mark,
applied on his words, and nothing automatic applies it. Until DRE-6226, DRE-6227
and DRE-6228 the pipeline did: the sweep put it on every WORKBENCH and OPERATOR
card it carried to Hand-work, and the record and retired-repo writers set it at
creation. Read live on 2026-10-09, 67 open cards carried it, and 49 of them had
it from the pipeline's own identities. Those writers have stopped. This pass
handles the cards already marked.

The operator decided on 2026-10-08 that no pipeline-applied `hand-built`
survives this migration. There is no switch that keeps an automatic card out.

The script is `scripts/hand_work_migration.py`, rewritten in place. It held the
first migration (DRE-5323), which moved person cards out of Todo, and
[`hand-work-migration.md`](hand-work-migration.md) is the record of that run.

## How to run it

Run each step from a bureau-pipeline checkout on `main`, with the operator's
Linear key. Read the output of each step before running the next.

```
python3 scripts/hand_work_migration.py census          # every card with its origin, class and action
python3 scripts/hand_work_migration.py run             # the dry run: prints the same, writes nothing
python3 scripts/hand_work_migration.py run --apply     # makes the writes
```

Three switches work on both commands, and each takes one or more values:

- `--include DRE-N` treats a named card as automatic, whoever marked it.
- `--include-actor NAME` treats every card that account marked as automatic.
- `--operator-step DRE-N` classes a named code card as an operator step.

If the script cannot read Linear, or the identity declaration, it stops before
any write, says so, and exits non-zero. It never treats an unreadable board as
an empty one. With `--apply` it re-reads each card's lane before writing to it,
and refuses a card that moved since the census. Such a card is left untouched.

## Who put the label on

The population is every open card carrying `hand-built`, in any lane. Done,
Canceled and Duplicate cards are not read.

The actor is the person or identity named on the history entry that added
`hand-built`. If it was added more than once, the newest entry counts. If no
entry added it, the label was set when the card was created, so the actor is the
card's creator. Linear writes no history row for a label set at creation. If
Linear did not return the whole history and no entry in it added the label, the
script does not guess.

| The actor | The card |
| -- | -- |
| One of the non-human identities in `config/linear-identities.json` (the fleet, operator-tools and sandbox users, read off that file), or an account named by `--include-actor` | **Automatic** — handled below. |
| Anyone else | **Kept** — listed as "kept — applied by hand by <actor>", untouched. |
| No actor at all | **Could not tell** — listed as "could not tell — left alone", untouched. |

## What happens to each automatic card

The classes are tried in this order, and the first one that matches wins.

| Class | The rule | What the script does |
| -- | -- | -- |
| epic | `epic_todo_gate.is_epic_card`: the shape stamp, an `[EPIC]` title, or any child. | Removes `hand-built`, adds `operator-step`. The epic stays in its lane. This is the operator's decision of 2026-10-08. |
| proof | `proof_and_demo.is_proof`: a `PROOF:` title. | Removes `hand-built` and nothing else. The proof keeps `no-code` and its lane, and the proof dispatch takes it from Hand-work. |
| operator step | Carries `no-code` or `needs-human`, or its title routes OPERATOR by `routing_verdict.title_verdict`, or opens `[OPERATOR]` or `OPERATOR:`, or it is named by `--operator-step`. | Removes `hand-built`, adds `operator-step`. The card stays in its lane. |
| code | Anything else. | Removes `hand-built`, then goes by lane (below). |

Two standing cards that only receive posts fall under operator step by rule:
the hourly hygiene summary carries `no-code`, and the daily groomer proposals
card has an `[OPERATOR]` title. They get the operator's marker because the
operator session reads those summaries.

**A code card, by lane:**

- **In Hand-work or Backlog, with a critic pass on record.** It is not
  re-planned. The script retires every live routing verdict on the card with
  a `🪦 verdict-retired` note. The note opens the way the planning exit's does
  and names each verdict by its fingerprint, so every reader treats those
  verdicts as retired. Its words say the migration retired them and the card
  did not go back to Planning. Before the note, the script takes off any
  `operator-step` a retired verdict put on, by the planning exit's own rule
  (`routing_verdict.lifted_marks`), so the sweep does not read the card as a
  person's. Then it stamps `FLEET` through
  `routing_verdict.stamp_card`, with a reason that opens `hand-built migration:`
  and names the pass it read. A Hand-work card then moves to Backlog through the
  guarded write layer, only if it is still in Hand-work at that moment. A
  Backlog card stays where it is. On its next pass the sweep's `promote_ready`
  carries the card to Todo, through the one promoter Todo's writers clause
  names, on the gates every FLEET card passes. This script never writes Todo.
- **In Hand-work or Backlog, with no critic pass.** It moves to Planning, only
  if it is still in the lane it was read in. Planning's exit writes it a fresh
  verdict (DRE-4884).
- **In Intake, In Progress or In Review, or any other lane.** It only loses the
  label. Intake classifies it, and a card with a pull request is already
  underway.

**A critic pass on record** is read with `plan_critic`'s own record reader, and
only records the fleet user wrote count. For a card with no parent, the newest
`plan-critic: stage=one-off` record on the card must be `result=PASS`. For a
child, `plan_critic.post_release` on the parent epic's thread must say the
second critic released the plan.

## What a changed card carries

Every card the script changes gets one comment that opens with
`🧳 hand-built-migration:`. It says what changed (label removed, label added,
verdict restamped, moved to Backlog, moved to Planning), who put the label on,
and the rule it followed, quoting the CEO's rule of 2026-10-07.

The census and the dry run print every card grouped by action, with its lane,
origin, class and what happens to it, followed by the count for each action.

## The operator-backlog pass (DRE-6429)

A second one-time pass lives in the same script. Operator cards filed before
DRE-6428 sit in Backlog wearing `needs-human` + `no-code` with no hold stamp.
Their hold reads `manual`, and the sweep leaves a `manual` hold alone by
design, so nothing ever moves them. This pass converts them.

```
python3 scripts/hand_work_migration.py operator-backlog           # the dry run: lists, writes nothing
python3 scripts/hand_work_migration.py operator-backlog --apply   # makes the writes
```

It reads every Backlog card wearing both labels that is not an epic and not a
proof, and whose hold is `manual` or already `operator-step`. Each card is
listed with its repo, title and reason under one of four headings:

- **unblocked** — a `manual` hold, every blocker Done, Canceled or Duplicate,
  and no parent epic or one In Progress. It gains `operator-step`, the
  `🔒 hold: reason=operator-step` stamp, an OPERATOR routing verdict if it
  carries none, then the `🔓 hold lifted:` line, and moves Backlog → Hand-work.
- **waiting** — a `manual` hold with an open blocker or a parent epic not In
  Progress. It gains `operator-step` and the stamp, nothing else. The sweep
  lifts it when that clears.
- **due** — already stamped `operator-step`, with nothing left to wait on. It
  is lifted and moved, as the sweep would.
- **not touched** — already stamped and still waiting, or carrying a routing
  verdict that sends it anywhere but Hand-work. Nothing is written to it.

The card carries only those lines and the verdict; there is no
`🧳 hand-built-migration:` note. If Linear cannot be read the pass stops before
any write and exits non-zero. With `--apply` it re-reads each card's lane and
refuses one that left Backlog since the read, and the move itself is
conditional on Backlog. Running it a second time writes nothing: the moved
cards have left Backlog and the waiting ones now read `operator-step`.
