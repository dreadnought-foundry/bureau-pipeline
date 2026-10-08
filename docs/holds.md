# Holds — every `needs-human` writer, why it holds, and what lifts it

The `needs-human` label is the pipeline's hold. A card that wears it is left
alone by the sweep, the fix loop's dispatch, the medic and limit recovery. Until
DRE-6173 the label was written from fifteen places in six files, and nothing
listed them. The bare label was the whole record, so nothing could tell a review
cap from a dead-run cap, and nothing knew what would lift either.

Now one file lists them. `config/holds.json` holds the vocabulary and one row
per writer. `scripts/hold.py` is the one module that reads and writes it, and
every card of epic DRE-6172 reads the hold through it. `python3
scripts/hold.py check` finds every writer in the tree and fails on any it
cannot match to a row. This page is the human-readable version of that file.

This card changes no behavior. No writer was edited. Every row describes a
site as it stands on `main` today, and a later card of the epic switches each
one to `hold.apply`.

## The vocabulary

Ten reasons, five lift kinds and four readers, as exact strings:

- **Reasons:** `stranded-no-run`, `no-route`, `review-cap-spent`,
  `dead-run-cap`, `turn-cap-park`, `epic-rereview-twice`, `plan-critic-bound`,
  `fix-dispute`, `unfixable-check`, `manual`.
- **Lift kinds:** `run-started`, `repo-on-rail`, `new-head`, `unpark-marker`,
  `manual`. One more lift applies to every reason: `card-closed`, whenever the
  card is Done or Canceled. It is a rule, not a row.
- **Readers:** `sweep` (`reconcile.held`, `reconcile.live_promotion_refusal`),
  `fix-dispatch` (`reconcile.card_parked_for_human`, asked by
  `reconcile.fix_dispatch_blocked`), `medic` (`medic_retry.park_reason`) and
  `limit-recovery` (`limit_recovery._held`).

## Each reason, its lift and where the lift sends the card

The lift kind of each reason is fixed by the contract. `hold.py check` holds
every row to it, so a row cannot give itself a looser lift. The last column is
the hygiene lane's third write (DRE-6273), made after the label comes off and
the receipt is posted.

| Reason | Lifts by | Why | Where the lift sends the card |
| -- | -- | -- | -- |
| `stranded-no-run` | `run-started` | A 🧠 or ⏳ run receipt newer than the stamp proves a run started. The pipeline never starts that run itself — see below. | Nothing moves |
| `no-route` | `repo-on-rail` | Read off the card's current `repo:` label, never the stamp, because the usual fix is a person correcting the label. | Triage → Planning |
| `review-cap-spent` | `new-head` | The review re-triggers were spent on one head. A pushed fix is a new head, and the review runs on it again. | Green Light → In Review |
| `dead-run-cap` | `unpark-marker` | Only an operator's `unpark` resets the death budget, and its `dead-run-budget-reset` marker is the fact. | Nothing moves |
| `turn-cap-park` | `unpark-marker` | The same budget reset. Two turn-cap deaths past green are the most a card ever gets. | Nothing moves |
| `epic-rereview-twice` | `manual` | The park ends in Triage for an operator. The way back, `plan_critic.REAPPROVE_HOW`, is a person's act that leaves no fact a sweep can read. | Nothing moves |
| `plan-critic-bound` | `manual` | Every bound park ends in Triage for an operator, and the way back is the same person's act. | Nothing moves |
| `fix-dispute` | `new-head` | The fix loop's budget was spent on one head. A new head runs the review, the gate and the fix loop again. | Triage → In Review |
| `unfixable-check` | `new-head` | The red check was read on one head (its receipt is keyed `unfixable-check-hold @<sha8>`). A new head is read again. | Triage → In Review |
| `manual` | `manual` | A person chose it, or a writer created the card already held. Nothing a sweep can read says it is over. | Nothing moves |

A `manual` hold — `epic-rereview-twice`, `plan-critic-bound`, `manual`, and any
label with no stamp — is lifted only by `card-closed` or by a person. Outside
Done or Canceled, nothing in this epic lifts it.

A `new-head` hold that is still stuck on the new head is re-parked with a fresh
stamp by the review, the gate or the fix loop. For a fix dispute there is a
second way back: an **Operator decision** comment starts the fix agent directly
(DRE-3451), and that run's Announce step takes the label off itself. With the
label off, `reason_of` answers nothing, so the hygiene lane never meets that
card.

### `stranded-no-run` waits on a person's re-send

This is the one reason whose lift waits on something a person does. The sweep
skips a held card entirely, and the relay dispatches only when a card enters
Todo, so nothing in the pipeline starts a run on its own. The pipeline already
made its unattended re-send once, before it held the card (DRE-5743's Todo
re-send, the row's tried-first step), and a second one is the move that already
failed.

So the run that lifts this hold is the one a person's re-send starts. Moving
the card out of Todo and back in Linear dispatches a run with the label still
on — no build run refuses a held card, and `validate_card.py` reads the label
only when a card is created. The first run receipt newer than the stamp lifts
the hold within one hygiene pass, with nothing on the card for the person to
clear. `linear_ops.py unpark` is the other re-send, and it takes the label off
itself before it moves the card.

### `no-route` reads the card's current `repo:` label

The lift is met when the `repo:` label the card wears now names a key of
`config/repo-map.json`. The stamp's qualifier — `repo:<slug>`, or `repo:none`
for a card with no `repo:` label — only records what the sweep read when it
held the card. A person can fix an unroutable card two ways:

1. **Correct the label** — a typo, a wrong slug, or a missing `repo:` label
   added. This is the usual fix.
2. **Add the repo to `config/repo-map.json`** — onboard it to the dispatch
   rail.

Either one lifts the hold. A card still wearing an off-rail slug, or no `repo:`
label, does not lift, even if the slug in its stamp has since joined the rail.

## The stamp

Each writer posts one line, the first line of its own comment, under the
pipeline's own Linear identity, right after the label:

    🔒 hold: reason=<code> at=<qualifier or none> lifts=<lift-kind> by=<writer-file>

The qualifier follows the lift kind: the full head sha for `review-cap-spent`,
`fix-dispute` and `unfixable-check`, `repo:<slug>` for `no-route`, and `none`
for the other six. `hold.stamp_line` refuses any other pairing, so a wrong
qualifier fails at the writer and never at the lift. `hold.py apply` exits 2 on
the same input before it writes anything. A writer that lands the label itself
posts only the stamp, through `hold.post_stamp`: `dead_run.py park --reason
<code>` writes the label and Backlog, both or neither, and stamps
`dead-run-cap` or `turn-cap-park` only once both have landed (DRE-6178).

A writer other than the hygiene agent that lifts a hold posts the lift line
through `hold.lift`, after the label comes off:

    🔓 hold lifted: reason=<code> because=<lift-kind or card-closed or operator> by=<writer-file>

The hygiene agent's own lift is its receipt instead: act `hygiene-hold-clear`,
tag `hyg-hold-cleared`, cause `reason=<code> because=<lift-kind>`.

Neither line may contain `budget exhausted`, `holding for a human`,
`held-for-human`, `needs-human`, `dead-run-requeue`, `turn-exhaustion-requeue`
or any tag in `config/pipeline-acts.json`. Each of those is a key some reader
already counts. Both comment writes are declared `not-an-act` in that file's
`unconverted` block: the stamp is the label's record and the lift line is its
receipt.

### A spent stamp is nobody's reason

The newest stamp on a card is its current hold, unless it is spent. Any one of
three newer comments spends every stamp older than it:

1. a comment opening with a `🔓 hold lifted:` line,
2. a comment carrying a `hyg-hold-cleared` receipt, or
3. a comment carrying the `dead-run-budget-reset` marker.

`hold.read_stamp` then answers nothing, as if the card had never been stamped.
A label standing over a spent stamp is a person's hold and reads `manual`:
every reader respects it, and nothing in this epic lifts it outside Done or
Canceled. This is what keeps a person's hand-applied label from being read as
an old machine reason whose condition is already met. A stamp written after
the retiring line is live again, which is how a re-park on the same head works.

## How a hold lifts

The hygiene agent's holds lane, `scripts/hygiene_holds.py` (DRE-6180, DRE-6273),
lifts a hold whose reason has cleared, once an hour, with nobody touching the
card, and sends the card back to the lane where the pipeline resumes it.

**What it reads.** Holds outlive the five lanes the hygiene board read covers,
so the lane reads its own candidates: one paged Linear query, fifty issues a
page, for every issue carrying `needs-human` in any lane. The query passes
`includeArchived: true` and selects `archivedAt`. Linear leaves archived issues
out of every issue query by default, and a Done card the team's auto-archive
put away is exactly the kind a stale label sits on. Each candidate's newest
live stamp is read with `hold.read_stamp`, the whole thread where the
fifty-comment window was cut short.

**What it lifts.** Five lift kinds. Three are finished by the label alone:

- `card-closed` — any card in Done or Canceled, whatever its stamp says and
  whether it has one. The cause names the stamp's reason, or `reason=manual`
  for a card with no live stamp. This is how the 207 stale labels counted on
  2026-10-07 come off.
- `run-started` — a 🧠 or ⏳ run receipt newer than a `stranded-no-run` stamp.
  The lane reads the receipt and never starts a run.
- `unpark-marker` — asked of `hold.lift_due` for a `dead-run-cap` or
  `turn-cap-park` stamp. An operator's `unpark` already moved the card.

None of those three moves the card. The other two leave a card in a lane
nothing resumes, so each also moves it (DRE-6273):

- `new-head` — the head sha of the card's open pull request differs from the
  stamped sha. The lane reads the head off the leg's own pull-request listing:
  the newest open pull request whose branch carries the card's identifier. No
  open pull request means no lift. A `review-cap-spent` card the sweep parked
  in Green Light (DRE-6181) goes from Green Light to In Review, and a
  `fix-dispute` or `unfixable-check` card the fix loop parked in Triage
  (DRE-6179) goes from Triage to In Review — the lane its open pull request
  says it is in. The push that made the new head has already started the
  review, and a head still stuck is re-parked with a fresh stamp on that head.
- `repo-on-rail` — the card's current `repo:` label names a key of
  `config/repo-map.json`. The lane reads the label off the card,
  never the stamp's qualifier, so a label a person corrected lifts the hold
  as much as a slug that joined the rail, and a `repo:none` stamp lifts once
  the card wears an on-rail label. A `no-route` card the sweep parked in
  Triage (DRE-6177)
  goes from Triage to Planning. The Triage lane leaves such a card alone
  while the hold stands (DRE-6190), so it is still there to be found. Planning
  and not Backlog: a card that has been through Triage since its newest
  routing verdict is refused `stale-verdict` in Backlog (DRE-4962), and
  Planning's exit routes it afresh.

The move is made only from the lane the hold parked the card in. A card a
person has already moved anywhere else is lifted and moves nothing.

A lift is two writes, in order: the label comes off, then the agent's own
receipt, act `hygiene-hold-clear`, tag `hyg-hold-cleared`:

    🧹 hygiene: hyg-hold-cleared — reason=<code> because=<lift-kind> · <HH:MM PT>
    evidence: <what was read>

The cause is `reason=<code> because=<lift-kind>`. The evidence names the
stamp's qualifier and the live fact that met it — the run receipt or budget
reset, the open pull request and its new head, or the live `repo:` label —
and, for a `new-head` or `repo-on-rail` lift, the lane it was read in; or
`lane Done` / `lane Canceled` for the universal lift. The receipt spends the
stamp it lifted, like every `hyg-hold-cleared` receipt.

**The third write is the lane move.** A `new-head` or `repo-on-rail` lift
from the lane the hold parked the card in adds a state write after the
receipt: In Review, or Planning. The label is already off when that write
fires a run, so the plan-gate does not refuse a plan run for it. Every other
lift moves nothing. In Review and Planning are both lanes the hygiene agent
may write (`hygiene.DESTINATIONS`); the lane proposes no other lane write and
no `gh` write.

**An archived card is unarchived for the write and re-archived after it.** A
card whose read carried `archivedAt` takes four writes: unarchive, the label
off, the receipt (whose evidence also says `archived`), and archive again. So
the lane never depends on whether Linear accepts a label write on an archived
issue, and the card ends archived as it began, with the re-archive's time as
its new `archivedAt`. The guard admits the archive write only on a Done or
Canceled card the read said was archived (DRE-6248). An archived card outside
Done or Canceled is left alone like any other open hold.

**What it leaves.** A `manual` hold — `epic-rereview-twice`, `plan-critic-bound`, `manual`, and any
label with no stamp — is lifted only in Done or Canceled. Anywhere else it waits
for a person, and the lane writes nothing on it. A label standing over a spent
stamp is the same person's hold, and the summary names it once as `held
manual` so somebody sees the label came back.

**One reset, one lift.** A budget reset retires the stamp it follows, so the
label over it reads `manual`. A fresh `dead-run-cap` or `turn-cap-park` stamp
after the reset is the card parked again with the reset's budget spent, and
it is not lifted by the reset that came before it. Only a reset newer than the
live stamp would meet the lift, and `unpark` takes the label off itself when it
posts one.

**A pass is bounded.** The lane lifts at most `HYGIENE_HOLDS_MAX_LIFTS` holds a
pass, default 40, so a pass makes at most about 160 Linear writes — four for
an archived closed card, three for a lift that moves the card. A value that
is not a positive integer stops the lane with the variable named. Order inside
the cap:

1. lifts met on an open card (`run-started`, `unpark-marker`, `new-head`,
   `repo-on-rail`), oldest stamp first;
2. then the `card-closed` lifts, oldest stamp first;
3. then the closed cards with no live stamp, by identifier.

A hold whose reason has just cleared lifts within one pass, whatever the stale
backlog behind it. Every lift over the cap is a summary row reading
`over the per-pass cap of <n> — carried to the next pass`, recommending a wait,
so an operator can watch the backlog drain. The pass that finishes the backlog
is the first with no carried row. A read-only dry plan on 2026-10-08 found 591
held cards across the fleet, 534 of them closed cards in the home leg — about
fourteen passes at the default.

## The reader rule

`hold.respects(labels, bodies, reader)` answers whether a reader stands down
for a card:

- no label — no;
- the label with no live stamp — yes;
- a stamp with a reason the registry has no row for — yes, failing closed;
- otherwise — whether a row carrying that reason names the reader.

Every reader asks it, with its own name (DRE-6182): `reconcile.held` and the
sweep's live re-read before a promotion, `reconcile.live_promotion_refusal`,
as `sweep`; `reconcile.card_parked_for_human`, which `fix_dispatch_blocked`
asks, as `fix-dispatch` beside its unchanged lane test; `medic_retry.park_reason`
as `medic`, whose sentence names the reason and its lift kind (`reason manual`
for a label with no live stamp); and `limit_recovery._held` as
`limit-recovery`. A spent stamp reaches each of them through `read_stamp`, so
a label a person put back by hand after a lift is read as `manual`, never as
the old stamped reason. The medic's and limit recovery's exception for a
Linear Sync or Merge Gate rerun (`medic_retry.park_rule_applies`) is by
workflow and stays where it is.

Every row names all four readers today, so no reader's answer changed when
they moved onto the registry. A later card that lets a reader through for one
reason changes that row's `readers`, and nothing else.

Some code still reads the bare label alone, and that is deliberate. Each of
these refuses or reports on any hold, whatever its reason — the same
fail-closed answer an unknown reason gets: the plan-gate
`dedupe_dispatch.parked_for_a_person`, the medic's run-log line
`limit_death_record.needs_a_person`, the proof dispatcher's
`proof_dispatch.first_run`, the Triage lane's `hygiene_triage.left_for_a_person`,
and `linear_ops.cmd_state`'s building-card guard. An exception for one of
them is a `readers` entry and a card of its own.

`hold.py reason <CARD>` prints the same answer `reason_of` gives for the live
card, for a shell step to read. It prints nothing when the label is off and
exits 0 in every case, including a failed Linear read.

## The anchor rule

A row names its site by three things:

- `file` — the file the site is in. A workflow step moved to `scripts/<name>.sh`
  is read through its workflow (`step_shell.workflow_source`) and keeps the
  workflow as its file, as `check_act_receipts.py` does.
- `scope` — the enclosing Python function (`<module>` at the top level), the
  workflow step's `name`, or the shell function in a script no step delegates
  to.
- `anchor` — a literal phrase in that scope's text. It is a phrase of the
  receipt the site posts beside its label write, or a constant the scope names.
  It is **never** the label-write line itself, so swapping `add_label` for
  `hold.apply` changes no row.

When one scope holds two sites, each site's text is a window of twelve lines
before and after it that never crosses the other site. An anchor matching no
site, or two, fails on the row. A site no row matches fails by its location. A
row changes only when its site moves to another scope or its receipt is
reworded, and `hold.py check` names the row.

The writers are discovered, never listed. In `scripts/**/*.py` the check reads
the code: a call to `add_label` with the hold label, a call to `dead_run.park`
(or to `park` inside `dead_run.py`), a tuple or list carrying `"needs-human"`,
and a `hold.apply` call. In `.github/workflows/*.yml` and `scripts/*.sh` it
reads the text: `add-label <card> needs-human`, `--label needs-human` and
`hold.py apply`. `linear_ops.py`'s create commands take the label as an argument
from their caller, so they are not a site. A card they create already held reads
`manual`.

## Every writer, and what is tried first

One row per site and reason. The tried-first step is what the pipeline does
without a person before it holds.

| Site (file · scope) | Anchor | Reason | Lifts by | Readers | Tried first |
| -- | -- | -- | -- | -- | -- |
| `scripts/reconcile.py` · `flag_stranded` | `WATCHDOG_TAG` | `stranded-no-run` | `run-started` | sweep, fix-dispatch, medic, limit-recovery | The sweep's one Todo re-send (DRE-5743) |
| `scripts/reconcile.py` · `flag_stranded` | `WATCHDOG_TAG` | `no-route` | `repo-on-rail` | sweep, fix-dispatch, medic, limit-recovery | The move to the operator's queue (DRE-6177) |
| `scripts/reconcile.py` · `hand_review_nudge_to_person` | `REVIEW_NUDGE_CAP_KEY` | `review-cap-spent` | `new-head` | sweep, fix-dispatch, medic, limit-recovery | Three review re-triggers on the head, then the Green Light question (DRE-6181) |
| `scripts/reconcile.py` · `hand_dead_run_to_planner` (In Progress or In Review, no PR) | `held-for-human` | `dead-run-cap` | `unpark-marker` | sweep, fix-dispatch, medic, limit-recovery | Two requeues, then one hand-off to the planner on a silent death (DRE-6178, DRE-6186) |
| `scripts/dead_run.py` · `_cmd_park` | `PARK_STATE` | `dead-run-cap` | `unpark-marker` | sweep, fix-dispatch, medic, limit-recovery | Two requeues, then one hand-off to the planner on a silent death (DRE-6178, DRE-6186) |
| `scripts/dead_run.py` · `_cmd_park` | `PARK_STATE` | `turn-cap-park` | `unpark-marker` | sweep, fix-dispatch, medic, limit-recovery | One requeue after a death past implementation green (DRE-4366) |
| `scripts/rereview_watch.py` · `_fire` | `BOUND_PARK_LANE` | `epic-rereview-twice` | `manual` | sweep, fix-dispatch, medic, limit-recovery | The watcher's first firing asks for the review again |
| `.github/workflows/plan.yml` · First critic — the bound parks in Triage | `the plan goes on to neither the second critic nor Green Light` | `plan-critic-bound` | `manual` | sweep, fix-dispatch, medic, limit-recovery | The pipeline's own second chance |
| `.github/workflows/plan.yml` · Review — the review died | `PARK_NOTE` | `plan-critic-bound` | `manual` | sweep, fix-dispatch, medic, limit-recovery | The pipeline's own second chance |
| `.github/workflows/plan.yml` · Review — the second critic passed a plan the first critic held | `plan_critic.py held-park` | `plan-critic-bound` | `manual` | sweep, fix-dispatch, medic, limit-recovery | The pipeline's own second chance |
| `.github/workflows/plan.yml` · Review — the second critic produced no result | `could not decide on this plan` | `plan-critic-bound` | `manual` | sweep, fix-dispatch, medic, limit-recovery | The pipeline's own second chance |
| `.github/workflows/plan.yml` · Re-check the revised plan — review mode | `the revision did not pass the plan's own gates` | `plan-critic-bound` | `manual` | sweep, fix-dispatch, medic, limit-recovery | The pipeline's own second chance |
| `.github/workflows/plan.yml` · Review — the bound parks the plan in Triage | `nothing reaches Green Light until a person settles it` | `plan-critic-bound` | `manual` | sweep, fix-dispatch, medic, limit-recovery | The pipeline's own second chance |
| `.github/workflows/agent-fix.yml` · Escalate checks the loop structurally cannot fix | `unfixable-check-hold` | `unfixable-check` | `new-head` | sweep, fix-dispatch, medic, limit-recovery | The fix run itself, which read the check |
| `.github/workflows/agent-fix.yml` · Report (`park_for_human` in `scripts/report_fix_result.sh`) | `park_for_human` | `fix-dispute` | `new-head` | sweep, fix-dispatch, medic, limit-recovery | The fix loop's rounds, up to its budget |
| `scripts/model_adoption_actions.py` · `<module>` (`QUESTION_LABELS`) | `The question card` | `manual` | `manual` | sweep, fix-dispatch, medic, limit-recovery | None — a person, or a writer creating a card already held, chose it |

`scripts/dead_run.py`'s site is `dead_run.py park`, which the build run's Report
step (`scripts/report_agent_result.sh`) calls when either budget is spent.

## Which holds are tried first, and how

One row per reason. The receipt is the phrase on the card that records the
attempt. `hold.py check` fails a row whose receipt occurs nowhere under
`scripts/` or `.github/workflows/`, so no row can name an attempt nothing
records.

| Reason | What runs first, with no person | The receipt that records it |
| -- | -- | -- |
| `stranded-no-run` | The sweep re-sends a card that sat in Todo with no run, once (DRE-5743). | `card sat in Todo with no run — re-dispatched` |
| `no-route` | The card moves to the operator's queue under the `card-stranded` act. The move lands with DRE-6177. | `card-stranded` |
| `review-cap-spent` | Three review re-triggers on the head, then the evidence-built Green Light question. The question lands with DRE-6181. | `review-nudge-cap` |
| `dead-run-cap` | Two requeues at the same budget, then — on a silent death, with no pull request and no blocker note — one hand-off to the planner. The Report step's cap has asked `dead_run.decide` since DRE-6178, and the sweep's two no-PR caps ask it through `hand_dead_run_to_planner` since DRE-6186; what the sweep sees at its cap is always the silent death. A credential refusal or an API or model death at the cap holds as today, because neither says anything about the card's size. | `✂️ dead-run-cap → Planning:` (`hold.DEAD_SPLIT_MARK`) |
| `turn-cap-park` | One requeue at the same budget after a death past implementation green (DRE-4366). | `turn-exhaustion-requeue` |
| `epic-rereview-twice` | The watcher's own first firing asks for the second critic's review again. | `asked for the review again` |
| `plan-critic-bound` | The pipeline's own second chance before each park: a revision round after a send-back, a re-run at a higher turn ceiling after a turn-cap death, a second ask after no result. | The round record `plan-critic: stage=<stage> round=<n>`, composed in `plan_critic.py` as `{MARKER_PREFIX} stage={stage} round=`, and the 🔁 notices |
| `fix-dispute` | The fix loop's rounds on the pull request, up to its budget. | `Fix budget exhausted` |
| `unfixable-check` | The fix run itself, which read the red check and found nothing to fix. | `unfixable-check-hold @` |
| `manual` | None — a person, or a writer creating a card already held, chose it. | — |

## The merge gate does not read the hold

The CEO decided on 2026-10-02 that `needs-human` does not block merging.
`tests/test_hold_registry.py` holds `scripts/merge_gate.py` and
`scripts/merge_sweep_gate.py` to it: neither may read `HOLD_LABEL` or the
literal `needs-human`.
