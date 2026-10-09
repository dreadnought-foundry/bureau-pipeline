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

**The script has been rewritten.** On 2026-10-09 `scripts/hand_work_migration.py`
became the hand-built migration (DRE-6230), which takes every automatically
applied `hand-built` off once — see [`hand-built-migration.md`](hand-built-migration.md).
The commands below, and the four classes after them, are the record of the
first migration as it ran; the `--only` switch no longer exists.

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

- **A person card with a run receipt newer than its person mark.**
  The mark is when `hand-built` or `no-code` was added, read from the card's
  history. If the mark is not in the history (it was set when the card was
  created, or is older than the history window), the script uses the card's
  creation time, so any run receipt counts as newer. A run that started after
  the card was marked is something a person should look at before the card
  moves.

  Only a real run counts as a run receipt (`is_run_receipt`, DRE-5877):
  - a `⏳` heartbeat, which a run posts once it has started (`⏳ 1/5 plan`, and
    so on);
  - a `🧠 model-attempt:` line that names an agent starting. A dispatched
    build posts `… — engineer agent starting (turns=…)` before the agent gets a
    turn, and the planner posts `… — planner agent starting.`.

  Other `🧠 model-attempt:` lines record a model reading the card. No run was
  dispatched at it, so they do not count:
  - the Planning classifier's `… — planning classifier read the card.`;
  - the groomer's `… — groomer judgement ranked the census …`, which it posts
    on its standing card every morning.

  Before DRE-5877 the script counted any `⏳` or `🧠` comment as a run. That is
  how it skipped DRE-4968 and DRE-4541 on 2026-10-04 (reading 2 below).
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

Written for the proof card DRE-5349 on 2026-10-05 between 08:30 and 09:15 PT.
Every reading was taken from live sources: the live Linear board, our own
database (`make db-read`, read-only), the live console, and the live Actions
runs. Nothing here comes from a test. A reading that did not come out the way
the card expected is written down as it read.

**Summary.** Six of the eight readings are clean. Reading 2 is clean on the
migration, but it found a defect in the script's skip rule. Reading 5 does not
match the numbers the card predicted, because the Green Light Hand-work view no
longer works the way the card describes it. The card stays open until someone
decides what to do about readings 2 and 5.

| # | Reading | Result |
| -- | -- | -- |
| 1 | The sweep's own move | clean |
| 2 | The migration | clean run; 2 false skips, a defect in the skip rule |
| 3 | Todo afterward | clean: 0 cards |
| 4 | The groomer | clean |
| 5 | The console | does not match the card: 24 rows on the view, 12 `no-code` cards in the lane |
| 6 | The epic refusal | clean; 0 live receipts |
| 7 | The harness | clean (one check could not be evaluated) |
| 8 | The morning briefing | clean |

The release these readings observe is two merges on bureau-pipeline: #708
(DRE-5322, the sweep carries a WORKBENCH or OPERATOR card to Hand-work), merged
at 16:13 PT on 2026-10-03 as `f1687ce94`, and #710 (DRE-5323, the migration
script), merged at 17:18 PT on 2026-10-03 as `1b48c8cdd`.

### 1. The sweep's own move

The first card the sweep promoted into Hand-work after #708 merged was
**DRE-5412**, at 16:25 PT on 2026-10-03, twelve minutes after the merge. The
receipt on the card, posted by Agent-Bureau:

> 🧹 Auto-promoted Backlog → Hand-work: routed **OPERATOR** — Not code — a
> deploy, a migration run, a secret. operator, your turn in Hand-work — a person
> builds this; nothing was dispatched. Marked `hand-built`, `no-code`.

- Linear's history shows the move `Backlog -> Hand-work` by Agent-Bureau at the
  same second. The card's routing verdict is `🧭 routing-verdict: OPERATOR`
  (posted 18:58 PT on 2026-09-30).
- Its labels when it landed included `hand-built` and `no-code`. The card's
  history has no label change before the move, so both were on the card from
  its creation.
- Nothing was dispatched at it. There is no `⏳` or `🧠` run receipt on the card
  after the move, then or since. The next things on the card are the
  unlanded-work watchdog's notice (08:03 PT on 2026-10-04) and the operator's
  own proof work.
- No earlier receipt exists. A search of every comment on the board for
  `Auto-promoted Backlog → Hand-work` finds 11 receipts, and DRE-5412's is the
  oldest. The other ten are DRE-5095 (11:08 PT on 10-04); DRE-3447, DRE-5169,
  DRE-5407, DRE-5409 and DRE-5591 (12:38 to 12:39 PT); DRE-5194 (12:42 PT);
  DRE-5629 (13:46 PT); DRE-5798 (18:29 PT on 10-04); and DRE-5377 (08:20 PT on
  10-05). Each says `Marked hand-built, no-code`.
- I did not find the Actions run that posted the 16:25 PT receipt. bureau-pipeline's
  and portico's `reconcile.yml` have no run between 16:10 and 16:26 PT. The
  receipt and the history entry are the evidence.

### 2. The migration

The operator ran it with `~/Documents/agent-bureau-tracker/handwork-migrate.sh`,
which downloads bureau-pipeline at a `main` that contains #710 and runs the four
steps with the operator's Linear key. It ran from 07:27:13 to 07:27:30 PT on
2026-10-04, against `main` at `1b48c8cdd`. The script saved its whole output to
`handwork-migration-2026-10-04-0727.log` in that folder, and everything below is
quoted from that log.

**The census before (07:27:16 PT).** Todo held 5 cards. All 5 were `person`
cards. There were **0 `epic`, 0 `finished` and 0 `build`** cards, and none
`unclassified`.

| Card | Labels | Verdict | Parent | What the census said |
| -- | -- | -- | -- | -- |
| DRE-5599 | repo:agent-bureau, initiative:bureau, agent:engineer, hand-built | WORKBENCH | DRE-5577 | moves to Hand-work |
| DRE-4973 | repo:bureau-pipeline, initiative:bureau, agent:ops, hand-built, no-code | OPERATOR | DRE-4967 | moves to Hand-work |
| DRE-4968 | ceo, hand-built, repo:bureau-pipeline, no-code, agent:ops, initiative:bureau | OPERATOR | DRE-4963 | skipped (see below) |
| DRE-4716 | repo:agent-bureau, initiative:bureau, agent:ops, hand-built, no-code | OPERATOR | DRE-4678 | moves to Hand-work |
| DRE-4541 | hand-built, repo:agent-bureau, initiative:bureau | none | none | skipped (see below) |

The two skip reasons, as the script printed them:

```
DRE-4968 → skipped — a dispatched run receipt at 2026-09-26T16:30:45.991Z is newer than its person mark (2026-09-26T16:28:53.060000+00:00) — a run started after the card was marked, so it is not moved
DRE-4541 → skipped — a dispatched run receipt at 2026-10-02T13:18:33.772Z is newer than its person mark (2026-09-21T23:36:08.531000+00:00) — a run started after the card was marked, so it is not moved
```

**The dry run (07:27:17 PT)** printed the same five cards with the same
outcomes, then `dry run — nothing was written. Re-run with --apply.`

**The `run --apply` (07:27:22 PT)** printed:

```
DRE-5599 → Hand-work
commented on DRE-5599
DRE-4973 → Hand-work
commented on DRE-4973
DRE-4716 → Hand-work
commented on DRE-4716

moved 3 card(s) to Hand-work: DRE-5599, DRE-4973, DRE-4716
```

Each of the three carries the `🧳 hand-work-migration: moved Todo → Hand-work by
the one-time Hand-work migration.` comment, at 07:27:26, 07:27:28 and 07:27:29 PT.

**The census after (07:27:29 PT)** listed 2 cards, both `person`, both skipped
for the reasons above: DRE-4968 and DRE-4541.

**DRE-4541 was not moved by the migration.** The script skipped it, and the
operator moved it by hand at 07:35 PT, as described next.

**Both skips were false, and the cause is a defect in the skip rule.** The rule
at `1b48c8cdd` treated any comment that starts with `⏳` or `🧠` (`LIFE_PREFIXES`
in `scripts/hand_work_migration.py`) as a run dispatched at the card. Neither
receipt was a run dispatched at the card:

- DRE-4968's receipt at 09:30 PT on 2026-09-26 is `🧠 model-attempt … planning
  classifier read the card`. That is the Planning classifier, not a build.
- DRE-4541's receipt at 06:18 PT on 2026-10-02 is the groomer's own
  `🧠 model-attempt … groomer judgement ranked the census`. The groomer posts
  that receipt on its standing card every morning. So **the script can never
  move DRE-4541**, which contradicts the paragraph "The groomer's standing card"
  above. That paragraph says the card "moves like any other".

The operator moved both cards by hand at 07:35 PT on 2026-10-04, with the
comment "Moved to Hand-work by the operator (10-04, sweep-restart Step 0)".
Linear's history shows `Todo -> Hand-work` by `bureau-tools` for both. This is
reported to the operator as a defect and has not been filed as a card.

**Fixed by DRE-5877.** The skip rule now counts only a real run receipt
("When a card is skipped" above). The classifier's receipt on DRE-4968 and the
groomer's on DRE-4541 no longer hold a card, so the script now moves the
groomer's standing card like any other person card.

**The "Before the run" paragraph, checked against Linear's history between 08:30 and
08:45 PT on 2026-10-05:**

- DRE-3621 moved `Todo -> Intake` at 15:24 PT on 2026-09-29, by `bureau-tools`.
  It moved `Green Light -> In Progress` at 07:30 PT on 2026-09-30 and stayed In
  Progress until it was closed Done at 17:09 PT on 2026-10-02, when every child
  had finished. Both facts hold.
- The two finished cards the operator hand-closed are not in Todo. Todo holds no
  card at all (reading 3).

### 3. Todo afterward

Read from our database at **08:45 PT on 2026-10-05**, and again at 08:51 PT:
**Todo holds 0 cards.**

- That is more than one full sweep after the migration. The sweep posted
  promotions at 11:08, 12:38, 13:46 and 18:29 PT on 2026-10-04 and at 08:20 PT
  on 2026-10-05 (reading 1).
- The hand moves at 07:35 PT on 2026-10-04 came before the reading.
- No card in Todo lacks a run receipt, because Todo holds no card.

Todo was also empty at 09:05 PT on 2026-10-04, as the harness printed it in
reading 7.

**The empty lane is not, by itself, proof that the mechanism works.** The fleet
restart of 2026-10-03 kept new work out of Todo while In Progress, In Review and
Triage were cleared first, so part of this emptiness is that policy. What shows
the mechanism working is reading 1 and the ten sweep receipts after it: the
sweep put each person's card in Hand-work, and none went to Todo.

**The known way back to Todo** is the console's hand-completion, which promotes
a finished card's dependents to Todo and does not hold back a WORKBENCH card
marked only `hand-built`. It produced no such card in this reading.

### 4. The groomer

The first scheduled groomer run after the migration was bureau-pipeline
`self-groomer.yml` **run 37314573171** (event `schedule`). It started at
06:08 PT on 2026-10-05, and its `post` job ran from 06:17:59 to 06:18:12 PT.

- The job log reads `card: DRE-4541` and
  `groom-verify: 29 card(s) … id 7c67df2ec85f → d50db9746997`.
- On the board, the comment `🧺 groom-proposal: d50db9746997` (cycle 15) was
  posted on DRE-4541 at **06:18 PT on 2026-10-05**.
- DRE-4541 had been in Hand-work since 07:35 PT on 2026-10-04, so the groomer
  found its standing card in Hand-work and posted there.
- No groomer run happened between the migration and that morning. The scheduled
  run before it was 37017716663, at 07:07 PT on 2026-10-02. The groomer was off
  during the Agent-Bureau fleet pause.

### 5. The console

Read on the live console (app.agent-bureau.com), signed in as the CEO, from
08:49 to 08:51 PT on 2026-10-05. Our database was read at 08:51 PT.

- **The Green Light view's Hand-work filter showed 24 rows.**
- **The Hand-work lane held 13 cards, and 12 of them carry `no-code`.**

**These two numbers are not equal, so this reading does not match the card.**
The card expected the view to list only `no-code` cards from the Hand-work
lane. On the live console, the view lists every person's card in any open lane:

- All 13 cards in the Hand-work lane: DRE-4541, DRE-4716, DRE-5095, DRE-5349,
  DRE-5377, DRE-5407, DRE-5409, DRE-5412, DRE-5591, DRE-5629, DRE-5672,
  DRE-5774 and DRE-5798. This includes **DRE-4541, which is marked only
  `hand-built`**.
- 9 cards in In Review: DRE-3447, DRE-4753, DRE-4968, DRE-5072, DRE-5077,
  DRE-5194, DRE-5232, DRE-5346 and DRE-5459.
- 1 card in In Progress: DRE-4441.
- 1 card in Green Light: DRE-5765.

So the split the card predicted is not there. No hand-built-only card is missing
from the Green Light view, because DRE-4541 is listed in it. Every Hand-work card
is on the view, and nothing in Hand-work is hidden.

Whether the view or the criterion should change is a decision for the operator
or the CEO. The view's behavior has changed since DRE-3806, and the card's
description of it is older than that change.

**One River.** I opened each migrated card that is still in the lane in One
River (`?card=`) at 08:51 PT:

- **DRE-4716** shows `hand → Hand-work · 25.4h · Operator` and, under WHO ACTS
  NEXT, `Operator · hand-work — nothing is dispatched`.
- **DRE-4541**, the card moved by hand, shows `hand → Hand-work · 25.3h ·
  Operator` and the same WHO ACTS NEXT line.

The other two migrated cards had left the lane before this reading, so One
River no longer shows them as hand-work:

- DRE-5599, the WORKBENCH card marked only `hand-built`, was closed Done at
  11:04 PT on 2026-10-04.
- DRE-4973 was Done by 08:47 PT on 2026-10-05.

The reading was taken a day after the migration, not right after it.

### 6. The epic refusal, read without a write

Run from bureau-pipeline `main` at `442dc2c66`, with the operator's Linear key.
`epic_todo_gate.py` has no write path.

At 08:46 PT on 2026-10-05:

```
$ python3 scripts/epic_todo_gate.py explain DRE-5240
refused: a write of DRE-5240 to 'Todo'

🚫 epic-not-todo: DRE-5240 → In Progress

An epic is never dispatched: Todo is where a card waits for a build run, and nothing builds an epic. This epic is already approved: the CEO moved it to In Progress, and In Progress is where its children promote.

Nothing was written: the card stays where it is.
```

At 08:48 PT, against **DRE-4401**, a one-off that wears `agent:planner`. It
carries a `planning-shape: one-off` stamp, no `epic` stamp, has no children, and
its title has no `[EPIC]`:

```
$ python3 scripts/epic_todo_gate.py explain DRE-4401
allowed: a write of DRE-4401 to 'Todo' is not refused
```

DRE-5289 was tried first and printed `refused`. That was correct: it was
stamped one-off on 2026-09-30 and became an epic with six children the same
day. So it was not used as the one-off.

**Live `🚫 epic-not-todo:` receipts since release: 0.** A board-wide comment
search for `epic-not-todo:` finds only two routing verdicts that quote the tag
(DRE-5316 and DRE-5319). Our database's comment events hold no
`epic-not-todo: DRE-` comment.

### 7. The harness

The first `harness.yml` run on `main` after the migration was **run
37214926490**. It was started by a push of `205664376` at 08:57 PT on
2026-10-04, ran its scenarios at 09:05 PT, and concluded `success`.

- `lane_contract: PASS`, with `16 asserted, 0 failed, 39 skipped (phase not shipped), 1 unevaluated`.
- Hand-work's card count, as it printed it:
  `Linear carries 12 state(s): … Hand-work (12), … Todo (0), …`
- The one unevaluated check was the console's state list, which the harness
  could not read (`reporting UNEVALUATED, never a pass`). The skipped checks
  include the Hand-work entrance, exit and evidence checks, which wait on Phase 5.

### 8. The morning briefing

The first 06:30 PT briefing after the migration was **2026-10-05's**. It was
read at 06:31 PT, built at 06:31 PT and emailed at 06:31 PT. I read the stored
document from the `morning_briefing` table.

- Its ready-to-start list (`sections.today.todo`) is **empty**. It holds no
  migrated card and no card that carries `hand-built` or `no-code`.

The briefing before the migration was **2026-10-04's**, read at 06:33 PT. Its
ready-to-start list held 5 cards, all a person's work:

- DRE-4541
- DRE-4716
- DRE-4968
- DRE-4973
- DRE-5599

**All five left the list.** Three were moved by the migration, and two
(DRE-4541 and DRE-4968) were moved by hand at 07:35 PT. Whether the briefing
should gain a line for hand-work is DRE-5034's call against its mockup, as
DRE-5318 hands it off.

### What is still open

1. **The skip rule.** It counts a Planning-classifier or groomer `🧠` receipt as
   a dispatched run. That makes the script unable to move the groomer's standing
   card (reading 2), and the groomer paragraph above says otherwise. This is a
   defect for the operator to file. It is not filed here. *Since closed:*
   DRE-5877 changed the rule so only a real run counts.
2. **Reading 5.** The Hand-work view's count is not the lane's `no-code` count,
   because the view now lists every person's card in any open lane. Someone has
   to decide whether the criterion or the view should change.
