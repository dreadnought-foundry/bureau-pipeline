# Triage reasons — every write into Triage, and the reason it leaves on the card

The weekly agent report (DRE-7512) counted thirty-seven stops in one week that
carried no record of why they happened, so nobody could tell whether they
needed a person at all. A pause with a reason on the card can be triaged in
seconds. One without a reason has to be dug out of logs.

Now one file lists every place this repository moves a card into `Triage`.
`config/triage-reasons.json` has one row per place: where its reason is
written and a phrase from that reason, or why it writes none.
`scripts/triage_reasons.py` is the one module that reads it, and `python3
scripts/triage_reasons.py check` holds the file to the code. This page is the
human-readable version of that file. It copies the `needs-human` hold registry
(`docs/holds.md`, DRE-6173).

This changes no behavior at run time. No write site was edited, and no move is
refused, defaulted or warned on.

## How the check finds the writes

It keeps no list. It reads the writes `ready_lane_writers.writes()` discovers,
keeps the ones into `Triage`, and names each by its unit with
`green_light_rows.unit_of()`: `<file>#<function>` for a script and
`<file>#<step>` for a workflow. The callers of a function come from
`lane_callers.callers_of()`, and an anchor is matched with `hold.row_matches()`
against the reasoning unit's own text — the function's source, or the step's
text read through `step_shell.workflow_source`.

A **reason** is one of three things the pipeline writes on the card, by the
same writer, alongside the move: a card comment, a `🔒 hold:` stamp, or the
description of a card created directly in Triage. "Alongside" rather than
"before" because `linear_ops.cmd_state` returns False when a guard refuses a
move, so a reason posted ahead of the move could sit on a card that never
moved.

## What fails the check

- A Triage write no row names — named by its unit and the line it sits on.
- A row whose unit is not a Triage write any more — named by the unit.
- An anchor that is not in its `reason_at` text, or one that stands on the
  lane-write line itself — named by the unit and the anchor.
- A `reason_at` that is neither the unit nor a discovered caller of it — named
  by both. A workflow step has no caller, so its reason is its own.
- An `unreasoned` row with an empty `why`, a unit in both blocks, a unit named
  twice in one block, or a `reasons` row missing a field — named by the unit.
- A write whose destination the discovery cannot read — named by its line,
  because it could be a Triage write.

So a change that adds a Triage write cannot go green until it posts a reason
and names it, or says in the registry why it does not.

## Every unit, on 2026-10-18

The discovery finds 19 writes in 15 units. Every one already posts a reason,
so `unreasoned` is empty.

| Unit | Reason written at | Anchor | What the reason tells a reader |
| -- | -- | -- | -- |
| `pipeline_notice.py#_file_card_quietly` (reached from five workflows) | `pipeline_notice.py#_file_card` | `_description(body, missed)` | The card is created in Triage carrying the notice's own text, plus a line saying the console door was not reached and why. |
| `planning_escalation.py#park_unread` | the unit | `unread_park_note(identifier, reason)` | A park note says nothing has read the card, why its classification could not be read, and that the way back is a move to Planning. |
| `reconcile.py#escalate_out_of_planning` | the unit | `stall_park_note(ident, reason)` | A park note says why Planning produced nothing — a stall, the planner line's bound or a hold nobody was asked about — and the way back. |
| `reconcile.py#_route_to_defect_lane` | `reconcile.py#promote_ready` | `prose_blockers.card_refusal(` | The refusal names the description line that claims a dependency the board does not hold, and both fixes: set the relation or reword the line. |
| `reconcile.py#flag_stranded` | the unit | `isn't on the dispatch` | The watchdog's receipt says the card's repo is not on the dispatch rail and how to fix it, beside a no-route hold stamp naming the slug. |
| `rereview_watch.py#_fire` | the unit | `epic-rereview-twice` | A hold stamp says the epic was sent back for a second re-review, and the hold's reason names the way back. |
| `agent-fix.yml#Escalate checks the loop structurally cannot fix` | the unit | `--reason unfixable-check` | A card note names the red checks the fix loop cannot clear, beside an unfixable-check hold stamp at the head it read. |
| `agent-fix.yml#Report` | the unit | `--reason fix-dispute` | A fix-dispute hold stamp at the head says the fix loop's budget was spent on it and a pushed fix is the way back. |
| `plan.yml#One-off critic — park in Triage` | the unit | `--reason plan-critic-bound` | A plan-critic-bound hold stamp says the one-off critic's bound was reached, beside the critic's park note when it wrote one. |
| `plan.yml#First critic — the bound parks in Triage` | the unit | `the plan goes on to neither the second critic nor Green Light` | A park comment gives the first critic's finding this round and how to re-approve the plan once a person settles it. |
| `plan.yml#Review — the review died` | the unit | `plan-critic-death-note.md` | The death note and record say the second critic's run died and how, and the park note after the move says what is owed. |
| `plan.yml#Review — the second critic passed a plan the first critic held` | the unit | `plan_critic.py held-park` | A note read off the thread says the second critic passed a plan the first critic still held, so the two disagree. |
| `plan.yml#Review — the second critic produced no result` | the unit | `The second critic could not decide on this plan` | A park comment says the second critic could not decide on the plan as many times as the bound allows, so nobody has finished reading it. |
| `plan.yml#Re-check the revised plan — review mode` | the unit | `but the revision did not pass the plan's own gates` | A park comment says the planner's revision failed the plan's own gates, and which gate refused it. |
| `plan.yml#Review — the bound parks the plan in Triage` | the unit | `nothing reaches Green Light until a person settles it` | A park comment gives the second critic's finding, what the re-plan changed, and how to re-approve the plan once settled. |

`python3 scripts/triage_reasons.py check` prints the same list from the file,
so the code, not this table, is the record when the two differ.

## What the check cannot see

Every code path above leaves a reason, so a Triage stop with none came from
somewhere this check cannot read:

- **A hand move in Linear.** A person dragging a card into Triage writes
  nothing the pipeline can see or check (`ready_lane_writers.unseen_writers()`).
- **The relay (agent-bureau).** Moves made by code outside this repository are
  not discovered here.
- **A reason post that fails at run time after its move.** The check reads the
  source. It proves each site is written to post a reason, not that Linear
  accepted the post on a given run — several sites post with `|| true`, so a
  refused comment leaves the move standing without its reason.

Anything `ready_lane_writers` and `lane_callers` say they cannot see in their
own docstrings is unseen here too. Finding which of these produced the report's
rows takes the report's own rows, and is a follow-up card.
