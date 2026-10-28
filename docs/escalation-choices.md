# The escalation-choices block — a planning question the console shows as buttons

A card waiting on the CEO shows its question, its choices as buttons and the
recommended choice at the top of the console's card panel, and one click
answers it (DRE-6168, design approved 2026-10-07). This page is the producer's
half of that contract: the block a `🙋 planning-escalation:` note carries, and
the rules it must pass before it is posted. The console parses exactly this
block and nothing else. What the console posts back when the CEO clicks — his
signed answer, DRE-3785 — is the console's contract, not this one.

## Where it comes from

The planner writes its prose reason to its reason file under the rules it
always had — plain English, no code fence, no diff, no file path, no command —
and writes its choices as JSON to a **second file beside the reason**:

| Route | Reason file | Choices file |
| --- | --- | --- |
| Epic (`Plan epic`, and its next rung) | `$RUNNER_TEMP/planner-escalation.txt` | `$RUNNER_TEMP/planner-escalation-choices.json` |
| Roll-up (`Roll-up route — split into child epics`, and its next rung) | `$RUNNER_TEMP/planner-escalation.txt` | `$RUNNER_TEMP/planner-escalation-choices.json` |
| One-off revision (`One-off revision — the planner answers the critic`) | `$RUNNER_TEMP/one-off-question.md` | `$RUNNER_TEMP/one-off-question-choices.json` |
| One-off critic's own question (`One-off critic — decide`) | `$RUNNER_TEMP/plan-critic-one-off.md`, the critic's result file — its `QUESTION` line | the same result file — its `OPTION` lines |

The one-off critic's question is the one route whose choices are not a JSON
file: the critic writes them as `OPTION` lines under its `QUESTION`
(`standards/plan-critic.md`), and `scripts/plan_critic.py` builds the block
from them whenever there are two or more the validator below accepts, and
writes it last in the escalation text it hands the posting step (DRE-3910).

The posting step hands the file to `scripts/planning_escalation.py escalate
--choices-file <path>`. When it is valid, the writer appends it to the note as a
fenced block — the **last thing in the comment, after the closing ask** — so the
prose a Linear reader sees is byte for byte what it would be without it, and
`scripts/hygiene_green_light.py` reads the same reason it always read.
`planning_escalation.parse_choices` reads the block back out of a comment.

Every note carries the three declared lines — Finding, Question and
Recommendation — directly under its opening sentence, rendered by
`scripts/console_escalation.py`: lifted from the reason when it declares them,
completed from the block when it does not, or saying `none given — …` when
nothing recommended anything (DRE-3909). When a block rides with the lines,
the Recommendation line's answer is the recommended choice's label.

No block is added when:

- the call passes no choices file — the classifier's refusal and every other
  route that does not ask for one;
- the reason itself was refused — the note posts the refusal line alone;
- the note is the `✋ escalation-stood-down` record, which asks nothing.

These routes post prose only, and the console shows them without buttons: the
classifier's refusal and the `--transport` and `--rewrite` wordings.

## The screen — a decision for the CEO, or housekeeping (DRE-7961)

The three routes that pass `--choices-file` — the epic route's escalation step
(`Planner escalation — hand-planning parks for the CEO`), the roll-up route's
(`Roll-up route — check the split`) and the one-off planner's own question
(`One-off critic — escalate`, when the revision `asked`) — are **screened**
before anything is posted (`planning_escalation.screen`). A call is screened
whenever it passes the path, whether or not the file exists, and never when it
is a `--transport` or `--rewrite` call. A call that passes no path is not
screened and posts exactly what it did before.

A screened escalation goes to the CEO only when it passes all three rules:

1. **It names a decision.** The choices file exists, is JSON and passes the
   validation below.
2. **It offers two or three options.** The planner writes 2 or 3 choices
   (`planning_escalation.SCREENED_CHOICES`). The console still accepts 2 to 4
   (`MIN_CHOICES`, `MAX_CHOICES`), and that stays the block's contract.
3. **It does not say it is not a decision.** No phrase from
   `planning_escalation.NOT_A_DECISION_PHRASES` — "not a real question",
   "housekeeping" and the rest — appears, in any case, in the reason or in the
   block's `question`, `context` or `why`.

One that passes parks in Green Light with exactly the note it got before the
screen, block included. One that breaks any rule is **housekeeping**, and goes
to the operator: no `🙋 planning-escalation` note and no write to Green Light.
It gets one note, opening `🧹 planning-stall-park:` so the plan-gate does not
re-plan the card on entering Triage, which names the rule broken, says the card
is for the operator and not the CEO, quotes the reason unless it carries a
verdict marker, and names moving the card to Planning as the way back. Then the
card moves to `Triage` (`planning_escalation.HOUSEKEEPING_LANE`), the way
`park_unread` parks a card nothing could read (DRE-5975). A card that has
already left Planning, or carries a current routing verdict, is stood down as
before and gets neither.

The judgment the code cannot make is the planner's, in its brief and the five
prompt copies, in the same words: a real business choice is two or three
defensible options whose difference is a business outcome — a price, a policy,
what to make public, what a customer sees, scope or priority, or a risky or
destructive change. A fact an agent can look up, a mechanical step for a person
(lift a hold, set or clear a label, re-run or retry, move a card, merge, set a
secret or variable, fix a repo label) and a question with only one sensible
answer are housekeeping, and for those the planner writes the reason and no
choices file. The returned-child default reason the epic route's escalation
step writes itself passes the path with no file beside it, so it goes to the
operator under rule 1.

## The block

A fenced code block with the info-string `escalation-choices`, holding one JSON
object — the same house shape as the plan artifact's `kpis` and `ledger-check`
blocks. Not an HTML comment: Linear rewrites comment markdown into its own
dialect, and a fenced block survives that verbatim.

- `question`: one sentence.
- `context`: one short paragraph.
- `choices`: 2 to 4 items — and 2 or 3 on a screened route, above. Each has
  - `id` — a slug matching `[a-z][a-z0-9-]*`, unique within the block;
  - `label` — a few words;
  - `effect` — one line on what happens;
  - `preview` — optional, a few lines of plain text showing the result;
  - `outcome` — `proceed` (back to the build queue), `replan` (back to
    Planning) or `close` (cancel the card).
- `recommended`: the `id` of one choice.
- `why`: one sentence giving the reason for the recommendation.

These keys and no others.

## Validation

The writer checks the block before anything is posted
(`planning_escalation.choices_problem`):

- it has 2 to 4 choices;
- every `id` is slug-shaped and no two are the same;
- `recommended` names one of the choices;
- every `outcome` is one of `proceed`, `replan`, `close`;
- `question`, `context`, `why`, every `label` and every `effect` are non-empty
  and pass the same plain-words check the reason passes
  (`planning_escalation.jargon`), and no `label` or `effect` carries a card
  number;
- a `preview`, when present, carries no code fence and no verdict marker — it
  renders the result, so a time or a product string is fine;
- no key outside the contract, at either level.

The run log gets one line, `escalation-choices refused: <rule>`, for a block
that fails, and the same for a choices file that is not JSON. On a screened
route such a block names no decision, and the card goes to the operator under
rule 1 of the screen, the rule named in its note. A block that fails on a route
that is not screened falls back to a prose-only note: the card still escalates
and still parks, it is just not structured, and nothing about the refusal is
posted to the card.

## The example — DRE-5260

DRE-5260's real question, as the planner's revision asked it on 2026-10-07:

```escalation-choices
{
  "question": "Should the form's Answer panel say when its suggested edits were last read, and let you re-read them?",
  "context": "The Answer panel used to show a small gray line under its suggested edits with the time they were last read and a Refresh control. The restored panel no longer says, so a reader cannot tell whether a suggestion is minutes or hours old.",
  "choices": [
    {"id": "time-and-refresh", "label": "Time + Refresh",
     "effect": "The panel shows the time the suggestions were last read and a Refresh control that re-reads them.",
     "preview": "Proposals as of 3:42 PM · Refresh", "outcome": "proceed"},
    {"id": "time-only", "label": "Time only",
     "effect": "The panel shows the time alone, with no way to re-read from there.",
     "preview": "Proposals as of 3:42 PM", "outcome": "proceed"},
    {"id": "leave-it-out", "label": "Leave it out",
     "effect": "The panel stays as it is and this card is canceled.",
     "outcome": "close"}
  ],
  "recommended": "time-and-refresh",
  "why": "a time you cannot act on is a dead end"
}
```
