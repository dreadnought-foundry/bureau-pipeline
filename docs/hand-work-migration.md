# The Hand-work migration

A one-time move, run by hand, that takes a person's work out of Todo and puts it
in Hand-work (DRE-5323, epic DRE-5240).

Todo is the build button: a card that enters it makes the relay dispatch a
build. Until DRE-5322 the sweep also carried a WORKBENCH or OPERATOR card into
Todo, marked `hand-built`, so a proof or an operator job could wait there for
days next to the fleet's builds, and Todo looked like a stuck queue. The sweep
now carries those cards to Hand-work instead. This migration moves the ones that
were already sitting in Todo when that changed.

## How to run it

Run each step from a bureau-pipeline checkout on `main`, with the operator's
Linear key. Read the output of each step before running the next.

```
python3 scripts/hand_work_migration.py census                 # Todo, each card with its class and what will happen
python3 scripts/hand_work_migration.py run                    # the dry run: prints the same, writes nothing
python3 scripts/hand_work_migration.py run --apply            # moves the person cards
python3 scripts/hand_work_migration.py run --only DRE-N       # one named card, dry; add --apply to move it
```

If the script cannot read Linear, it stops before any write, says so, and exits
non-zero. It never treats an unreadable board as an empty one.

## The four classes, and what happens to each

Each card in Todo goes into exactly one class. The rules are applied in this
order, and the first one that matches wins.

| Class | The rule | What the script does |
| -- | -- | -- |
| `epic` | `epic_todo_gate.is_epic_card`: the shape stamp, an `[EPIC]` title, or any child. The `agent:planner` label is never read, because every classified one-off carries it too. | **Nothing.** The sweep's carry (DRE-5347) moves an epic it finds in Todo on its next pass: to In Progress if it was In Progress before Todo, otherwise to Planning. The census prints the lane it will go to (`epic_todo_gate.carry_lane`). |
| `finished` | An open card whose comments carry linear-sync's merge receipt (`✅ Merged:`) or `linear_ops.MERGED_NOT_CLOSED_MARKER`. | **Nothing.** Done is ground truth, and closing a card is a person's decision. The census quotes the receipt. The operator closes the card as Done by hand, or returns it to a named lane with a reason, before the proof reads Todo. |
| `person` | Carries `hand-built` or `no-code`. | **Moved to Hand-work** through the guarded write layer (`linear_ops.cmd_state`), only if the card is still in Todo at the moment of the write. Then it gets one comment that opens with `🧳 hand-work-migration:` and says why it moved. |
| `build` | None of the above: a card that a build run is dispatched or queued for. | **Nothing.** |

If the script cannot read a card's labels or comments, the card is
`unclassified` and left alone.

The census and the run print, for each card: its id, labels, class, routing
verdict (if it has one), parent, and what happens to it.

## When a card is skipped

- **A person card with a run receipt (`⏳` or `🧠`) newer than its person mark.**
  The mark is when `hand-built` or `no-code` was added, read from the card's
  history. If the mark is not in the history (it was set when the card was
  created, or is older than the history window), the script uses the card's
  creation time, so any run receipt counts as newer. A run that started after
  the card was marked is something a person should look at before the card
  moves.
- **A card named with `--only` that is not in Todo.** The migration only moves
  cards out of Todo, and never reaches into another lane.

The output prints the reason for each skipped card.

## The groomer's standing card

The groomer posts its morning proposal on one standing card, which it finds by
identifier from the `GROOM_PROPOSAL_CARD` repository variable (DRE-4541 today).
That card is a `person` card and moves like any other. The groomer still finds
it in Hand-work, for two reasons. It looks the card up by identifier, not by
lane. And `groom_schedule_gate.card_is_open` refuses only a completed or
canceled card, which Hand-work is not. The groomer's verify check also reads its
in-flight lanes from the lane contract (DRE-5348), so a replacement card in
Hand-work counts as approved and in flight.

## Before the run

The CEO counted Todo on 2026-09-29 and found 16 cards that were not waiting to
be built. Nine were a person's work, one was an epic, and two were finished but
never closed. The epic and the two finished cards left Todo by hand before this
migration was written:

- **The epic.** The operator moved DRE-3621 from Todo to Intake at 15:24 PT on
  2026-09-29 (Linear's history, actor `bureau-tools`). It was re-planned, and it
  has been In Progress since 07:30 PT on 2026-09-30, with its four open children
  in Backlog.
- **The two finished cards.** On the afternoon of 2026-09-29 the operator closed
  eight Todo cards as Done by hand, between 10:41 and 16:00 PT. The two finished
  cards were among them.

At 08:49 PT on 2026-09-30, Todo held 13 cards:

- 9 person cards: DRE-4541, DRE-5181, DRE-3904, DRE-5072, DRE-4716, DRE-5087,
  DRE-4702, DRE-4968 and DRE-4973.
- 4 build cards: DRE-5100, DRE-5101, DRE-5102 and DRE-5105, children of DRE-5034.
- No epic.

The rules above mean the day of the run does not depend on that count. A card
of any class found in Todo on the day is handled and recorded, not skipped.

## The run

_Written by the proof card, DRE-5349: the census before, the dry run, the
`run --apply` output and the census after, each with its PT time._
