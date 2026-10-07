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

The posting step hands the file to `scripts/planning_escalation.py escalate
--choices-file <path>`. When it is valid, the writer appends it to the note as a
fenced block — the **last thing in the comment, after the closing ask** — so the
prose a Linear reader sees is byte for byte what it would be without it, and
`scripts/hygiene_green_light.py` reads the same reason it always read.
`planning_escalation.parse_choices` reads the block back out of a comment.

No block is added when:

- the choices file is missing — the normal case on every other route, and
  silent;
- the reason itself was refused — the note posts the refusal line alone;
- the note is the `✋ escalation-stood-down` record, which asks nothing.

These routes post prose only, and the console shows them without buttons: the
one-off critic's own question, the classifier's refusal, the returned-child
default reason the epic route's escalation step writes itself, and the
`--transport` and `--rewrite` wordings.

## The block

A fenced code block with the info-string `escalation-choices`, holding one JSON
object — the same house shape as the plan artifact's `kpis` and `ledger-check`
blocks. Not an HTML comment: Linear rewrites comment markdown into its own
dialect, and a fenced block survives that verbatim.

- `question`: one sentence.
- `context`: one short paragraph.
- `choices`: 2 to 4 items. Each has
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

A block that fails falls back to today's prose-only note: the card still
escalates and still parks, it is just not structured. The run log gets one line,
`escalation-choices refused: <rule>`, and nothing about the refusal is posted to
the card. A choices file that is not JSON is logged the same way.

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
