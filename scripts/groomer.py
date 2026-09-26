#!/usr/bin/env python3
"""The groomer — it sequences the whole Intake population into cycles, and the
CEO approves each batch before anything leaves (DRE-2683).

Nothing else in the system answers *how much, in what order, all at once*. The
critic answers whether one card can be built — the expensive question, asked one
card at a time. Planning (DRE-2719) answers whether one card is still wanted.
Both read a single card. **A batch is not a list of individually-good cards**,
and the facts that decide a batch only exist BETWEEN cards: two cards editing one
file, eleven children of one epic that belong in one cycle, a repo that waits
three months because another repo goes first.

The independent Forms review (DRE-2649) found file collisions nobody had
spotted — cards scheduled as if independent that both land in
`Thread.tsx` — and something reading one card at a time structurally cannot see
that. This module is the reader that sees the set.

## What it does, in order

  1. **Reads the whole lane.** Paginated (`linear_ops.gql_paged`). The sweep's
     `issues(first: 100)` with no cursor made its world the first 100 rows of a
     226-card Backlog, and WHICH rows it never saw was decided by Linear's
     default ordering (DRE-2681). A groomer that sequences page one has
     sequenced nothing, so completeness is asserted: every card in the
     population comes out carrying exactly one outcome.
  2. **Groups into units.** An epic and its children are ONE unit. Classifying
     eleven children of one Forms epic in three separate batches spreads one
     deliverable across three cycles for no reason, so the epic — not the
     card — is the atom of cycle assignment.
  3. **Finds the collisions.** Two cards citing the same file become an ORDER
     between those two cards, reported with the file that caused it.
  4. **Sequences.** Urgent first, then High, then everything else — OLDEST
     first — subject to the constraints above, deterministically (DRE-4725).
     Repo order is a tie-break inside a day and never the master key
     (DRE-3096).
  5. **Assigns cycles.** Linear's own primitive — cycles are enabled and cycle
     11 is running, so "which cycle" is expressible today without inventing a
     container.
  6. **Proposes.** Two lists drawn from the same twenty (DRE-4727): the
     Planning list, its order and the WHY on every row, and the Cancel list,
     a one-line reason on every row. Then what is deferred and what brings
     each one back, what could not be ranked at all, and — said out loud
     rather than discovered — which repos wait and roughly how long
     (DRE-3152).

## The order, top to bottom (DRE-3096, reversed by DRE-4725)

The CEO's decision on DRE-4669 (2026-09-23): "work through the old Intake
pile, 20 cards at a time, oldest first, until the pile is gone." Measured that
day, the 14-day window had left 74 of the 117 cards older than 14 days out of
every batch, for ever, and ordered the rest newest first.

  1. **Urgent** (Linear priority 1) opens the batch, every repo, oldest first.
     The production-issue lane: a card raised while debugging goes ahead of
     everything.
  2. **High** (2) next, oldest first — otherwise High means nothing.
  3. **Then everything else, OLDEST creation day first.** There is no window:
     no card is left out of the batch for being old, and `--window-days` is
     still accepted but excludes and orders nothing.
  4. **Repo order is a tie-break inside a day** — Portico first only among
     cards of equal priority created on the same day; then the exact
     timestamp, then the identifier.
  5. **Collisions and blockers are ordering constraints**: the older card of
     two naming one file goes first, a blocker goes before what it blocks, and
     an Urgent or High card waiting on an older unprioritised card pulls that
     card forward ahead of itself. Never a membership filter.
  6. **The date is the CREATION date**, never the last update. A stray agent
     comment must not move a card.

The epic is still the unit, so an epic's band is the highest priority among
the epic and its children — one Urgent child pulls the whole unit into the
batch — and its age for ordering is its OLDEST card, so one new child does not
send an old epic to the back of the pile.

## Three outcomes, and only what the CEO agreed moves

`now`, `not-now`, `dead`. **"Not now" is first-class**: a card can be
well-formed, wanted, and correctly left alone for a month, and without a "later"
the only way to say it is to say "no". **"Dead" is the Cancel list — a
recommendation `propose` never acts on, and the drain executes only once the
CEO agrees** (DRE-4727, DRE-4733). The CEO's decision on DRE-4669 (2026-09-23): when
the groomer looks at the twenty oldest cards it also decides whether each one
still applies. So the morning's `capacity` cards are Planning plus Cancel
together, walked in the rules' order: a card whose description carries a
`Superseded by:` line, or that the read called `likely-done`, goes on the
Cancel list; the rest go on the Planning list. A card outside the twenty waits
its turn whatever it says — it is not cancelled early. Every Cancel row carries
a one-line reason naming the card or merged PR that replaced it, because a
recommendation nobody can check is one nobody should act on; the drain cancels
it once the CEO agrees — to `Canceled`, never `Done` — with that reason written
on the card first. In the 2026-08-22 sweep
the recommendation, the decision and the execution were three separate steps,
and the executing agent caught an error in its own brief precisely because it
was working from an explicit list rather than its own judgement — and they
still are: `propose` proposes, the CEO agrees, the drain executes.

## The approval gate

`propose` writes nothing but the proposal comment `--post` asks for, and writes
that one at most once: it reads the card first and skips a proposal already
there, so a retried run adds no duplicate (vendor boundary Q3).

`drain` moves the approved Planning list out of Intake and into Planning, and
the approved Cancel list into Canceled with each card's reason commented on it
(DRE-4733). It moves **the batch that was APPROVED, read from the record**
(DRE-3338): the approval names a proposal id, the proposal comment carrying that
id is the record, and the cards in its two tables are the cards that move, in
that order. An
approval written by the pipeline's own Linear identity is refused: a gate the
proposer can pass by itself is not a gate.

The CEO says more than yes or no (DRE-3370). Beside `groom-approved` the card
carries `groom-declined: <id> — <reason>`, `groom-excluded: <id> DRE-N` and
`groom-added: <id> DRE-N`, all read the same way — anchored at the start of the
comment, emoji optional — and all honoured only when their author is not the
pipeline. The drain moves the approved lists MINUS the exclusions (on either
list) PLUS the additions (to Planning), reads the WHOLE thread to find them,
and writes one
`groom-drained: <id>` record of what it did; every refusal is written down as
`groom-drain-refused: <id> — <reason>`, and a batch that already carries a
drained record is refused, so a second dispatch moves nothing.

And the CEO can switch a whole REPO off (DRE-3403). `🧺 groom-hold-repo: atlas`
on the card leaves atlas out of every proposal until
`🧺 groom-release-repo: atlas` switches it back on: the cards are removed
before the census is built, so none of them is sent to the model, ranked,
given a cycle or counted against `--capacity`, and the page lists the repo once
as held. The lane's population and census are untouched — the work is still
there, it is just not on offer. The drain reads the same markers at drain time,
so a hold written after the proposal was posted holds those cards back (`held
back`, `repo held: <slug>`) while the rest of the batch moves. Not to be
confused with the per-repo work-in-progress cap, which stops BUILDS after
classification; this stops the cards being proposed at all.

And a decision the CONSOLE writes counts as the CEO's (DRE-3754). The console
holds only the fleet's Linear key, so a signed-in owner's Approve, Decline or
"not this one" is authored by the pipeline's own identity — and refused by the
rule above, which is how the CEO came to be told to paste a Linear key before
every decision. Now the console signs: an Ed25519 receipt on the comment's last
line, over the marker, the card, the batch, the console user and the time,
made with a key no workflow or agent holds (`console_receipt.SPEC`). A
pipeline-written marker carrying a receipt that verifies decides; one without a
receipt is refused exactly as before; one whose receipt fails is refused with
the reason in the record. The CEO's own Linear user is read as it always was.

And the next proposal ANSWERS the last decline (DRE-3373). `propose --post`
reads the card first, and when it finds a decline newer than the last proposal
there — unapproved, reasoned, not written by the pipeline — the page opens with
the reason quoted back verbatim and one sentence naming what changed in this
batch relative to the declined one, by id, computed from the two batch lists
and never asked of a model. A proposal with no decline behind it renders byte
for byte as it did before.

It used to rebuild the proposal from live state and refuse when the id it
re-derived differed from the approved one. That is a real safety property —
"the batch on the page is not the batch the drain would move" — said the wrong
way round, and it made the drain fail on two ordinary events: a card entering
Intake between propose and drain (DRE-3337 was filed five minutes after
proposal `f673bfefa340` was read), and the model answering the same census
slightly differently, which a 260-card judgement does. Each failure cost
another ~$6 model call, another eight minutes and another CEO approval, and
threw away an approval for a reason that had nothing to do with the batch. The
property is kept where it belongs: the drain reads the list the CEO saw. A card
on it that has moved since is reported as `already gone`, with the lane it is
in now, and the rest of the agreement still moves (DRE-4733) — it used to
refuse the whole batch, and a card already Done cannot be moved either way.

The cadence is three decisions, in order. D5 (DRE-2683, approved 2026-08-23):
**on demand, until the groomer's judgement has been audited.** The amendment
(DRE-3337, green-lit 2026-09-08): the drain may also be fired by the CEO's
Approve on the console, as a `groom-drain` `repository_dispatch` that reaches
`drain` and nothing else. The morning proposal (DRE-3586's signed answer,
2026-09-21, absorbed by DRE-4677): a `schedule` in `self-groomer.yml` runs
`propose` — and only `propose` — at 06:15 PT, behind a gate job that reads the
PT clock and the standing card (`groom_schedule_gate.py`, DRE-4688), so the
proposal is on the card before the 06:30 briefing. D5's stated cost — on demand
means it runs when someone remembers — no longer applies to the proposal; the
drain still waits for the CEO's Approve, and a proposal moves nothing.

CLI:

    python3 scripts/groomer.py census  [--lane Intake]
    python3 scripts/groomer.py propose [--lane Intake] [--capacity 20]
                                       [--batch-cycles 1] [--priority portico]
                                       [--window-days 14] [--no-judgement]
                                       [--out proposal.json] [--post DRE-N]
                                       [--keep-answer judgement-answer.txt]
                                       [--hold-repo atlas]
    python3 scripts/groomer.py drain   --card DRE-N [--lane Intake]

`--keep-answer` writes the ranked read's raw answer — every piece of a
continued one, joined — beside the proposal, for the run artifact (DRE-3331).
The first run that ever answered kept nothing, and the answer had to be bought
again before anyone could see that 204 of its 260 lines had been read off the
wrong field. Written only when a call answered; the artifact step ignores
absence. It is model output over card text: an artifact, never a comment.

`drain` takes no shaping flags beyond the lane: the batch it moves is READ off
the approved proposal record, so there is nothing left for a flag to shape.
It makes no model call — a drain is a move, not a judgement.

## The ranked read (DRE-3150)

Everything above is the RULES, and they are all this module had. `propose` now
also makes ONE bounded model call — the whole population as a census, against a
context pack of what is already in flight (`groom_context.py`), read once
through `groom_judgement.py` — and since DRE-4725 the read decides neither
the batch's membership nor its order.

**The rules' order is the order.** The batch is the oldest cards by the rules
above, `capacity` at a time; the model's `now` set no longer fills it, its
`now` order no longer reorders it, and a `not-now` no longer takes a card out.
What the read still does is per card: a card the answer omits or garbles is
`unranked` and comes OUT of the batch, and the next card in order takes its
slot — the proposed cards and the unranked ones are disjoint, asserted where
the proposal is written (DRE-3544); a card the read calls `likely-done` goes on
the Cancel list when it is in the morning's twenty, with its evidence as the
reason beside the regex's `superseded by DRE-N`; and every card carries the
read's reason. Every reason passes
`planning_escalation.refusal` before it is written, because the CEO reads
outcomes and never code.

`--no-judgement` makes no call at all and is today's groomer byte-for-byte, so
the audit card can run the two readings over one population.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocker_prose  # noqa: E402 — ONE anchored blocker-prose grammar (DRE-2922)
import console_receipt  # noqa: E402 — ONE reader of a console-signed decision (DRE-3754)
import dead_run  # noqa: E402 — ONE Pacific clock for a time a person reads
import groom_context  # noqa: E402 — the context pack (DRE-3150)
import groom_judgement  # noqa: E402 — the one ranked read (DRE-3150)
import intake_controls  # noqa: E402 — ONE reading of the operator's Intake switch
import linear_ops  # noqa: E402
import planning_classify  # noqa: E402 — the model receipt, one definition
import planning_escalation  # noqa: E402 — the plain-English write guard
import sanitize_untrusted  # noqa: E402 — ONE defang prefix for untrusted text

# --------------------------------------------------------------------------- #
# vocabulary                                                                   #
# --------------------------------------------------------------------------- #

# The three answers the groomer is allowed to give. Held here, in one place, so
# "later" cannot quietly collapse into "no": the moment those are the same
# answer, Intake is a pass/fail funnel again.
OUTCOMES = {
    "now": (
        "In the approved batch. Carries a cycle and a position in it, and it "
        "is the only outcome that moves a card."
    ),
    "not-now": (
        "Wanted, and deliberately not this batch. The batch is full, and it "
        "names the cycle it is reconsidered in — this is 'later', and it is "
        "not 'no'."
    ),
    "dead": (
        "Proposed for cancellation, on the Cancel list beside the batch. Names "
        "the card or merged PR that superseded it in a one-line reason; the "
        "drain cancels it once the CEO agrees, with the reason on the card."
    ),
}

# Where a dead RECOMMENDATION came from (DRE-3150). Two readers now propose
# one, and a recommendation the operator cannot attribute is one they have to
# re-derive: the description's own `Superseded by:` line, which a person wrote,
# and the ranked read, which a model wrote. Both land in the same list; only
# the first ever carries a `superseded_by` target.
DEAD_FROM_LINE = "superseded-line"
DEAD_FROM_JUDGEMENT = "judgement"

# The one sentence a reason the plain-English guard refused is replaced with.
# Written once, here, because the presentation card (DRE-3152) and the console
# cards render this exact string.
WITHHELD_REASON = groom_judgement.WITHHELD_REASON

# The heading the labelled reasons live under (DRE-3764). Written once and
# exported, because the console's reader finds the section by it — the same
# render-and-read-back contract `_BATCH_HEADING` carries for the table.
BATCH_REASONS_HEADING = "## Why each card is in the batch"

# The Cancel list's heading and header row (DRE-4727). Not ours to choose: the
# console's reader of the proposal comment (DRE-4682 in agent-bureau) is
# written against exactly these strings, and so is the drain that cancels an
# agreed card (DRE-4733). Exported so the console's mirroring tests can read
# them off the pipeline checkout. The section ends at the next `## ` heading,
# and it is ABSENT when nothing is proposed for cancellation — that absence is
# how the reader knows there is none.
CANCEL_HEADING = "## Cancel, with reasons"
CANCEL_COLUMNS = "| # | Card | Pri | Repo | Epic | Title | Reason |"

# The lane the drain writes the Planning list into: Intake's exit is a
# classification, and Planning is what produces one (DRE-2719).
DRAIN_TO = "Planning"

# The lane the drain writes an agreed Cancel row into (DRE-4733). `Canceled`
# and never `Done`: nothing was delivered, and `Canceled` clears the card
# without claiming it was. A module constant, and named at its ONE call site,
# because `ready_lane_writers.py` attributes a write by reading constants and
# parameter defaults — never a local.
CANCEL_TO = "Canceled"

# Terminal lanes the drain refuses outright. It cancels only what the CEO
# agreed, from the Cancel table, and it never closes a card: `Done` is what a
# merge says, and `Duplicate` is a judgement about two cards nobody asked it to
# make. Stated as data so the refusal is testable rather than implied by the
# absence of code.
NEVER_WRITES = ("Duplicate", "Done")

MARK = "🧺"
PROPOSAL_TAG = "groom-proposal"

# --- the CEO's decision vocabulary (DRE-3370) -------------------------------
# One marker was a yes/no question, and a batch is not one. The CEO reading a
# twenty-card proposal wants to say *yes, except these two* and *yes, and this
# one as well*, and the only way to say either used to be to re-run the
# groomer, spend another ranked read (~$6, ~8 minutes) and ask for a second
# approval of a batch that was right apart from two rows.
#
# All four are read the SAME way — anchored at the start of the comment, emoji
# optional, id `[0-9a-f]{6,}` (`decision_match`) — and all four are honoured
# only when their author is not the pipeline's own Linear identity. That last
# clause is the whole gate (DRE-2721): a marker the proposer can write is a
# credential the proposer can mint, so it decides nothing — approval included,
# which is why the self-approval refusal is unchanged by any of this.
APPROVAL_TAG = "groom-approved"
DECLINE_TAG = "groom-declined"       # `<id> — <reason>`; the reason is required
EXCLUDE_TAG = "groom-excluded"       # `<id> DRE-N[ — <reason>]`, per card
ADD_TAG = "groom-added"              # `<id> DRE-N[ — <reason>]`, per card
DECISION_TAGS = (APPROVAL_TAG, DECLINE_TAG, EXCLUDE_TAG, ADD_TAG)

# --- the CEO's repo switch (DRE-3403) ---------------------------------------
# Beside the decision markers, and deliberately NOT one of them: these are not
# about a batch. The CEO said three times that the Bureau repos — agent-bureau
# and bureau-pipeline — are the only ones proposed until the groomer is solid,
# and atlas cards were proposed anyway, approved as part of a batch, drained,
# and one was mid-planning before it was pulled back by hand (CEO decision,
# 2026-09-08). Saying "not that repo" three times and having it happen anyway
# is a missing switch, not a communication problem.
#
# So a repo the CEO switches off is left out of every proposal until it is
# switched back on. The marker names a SLUG and no proposal id, because a hold
# outlives the proposal it was written on — that is the whole point of a
# switch — and the newest marker per slug wins, so `groom-release-repo` is not
# a second mechanism but the same switch the other way up.
#
# Read the way every other marker is read (anchored at the start of the
# comment, emoji optional) and honoured only when the author is not the
# pipeline's own Linear identity. That gate matters most in the RELEASE
# direction: a proposer that could switch a repo back on could undo the CEO's
# own answer and propose the cards anyway.
#
# DISTINCT from the per-repo work-in-progress cap, which stops BUILDS after
# classification. This stops the cards being proposed at all.
REPO_HOLD_TAG = "groom-hold-repo"
REPO_RELEASE_TAG = "groom-release-repo"
REPO_TAGS = (REPO_HOLD_TAG, REPO_RELEASE_TAG)

# The drain's OWN records — written by the drain, never by a person, and read
# by the console and by the next drain.
#
# `groom-drained` is what the drain wrote down afterwards (DRE-3326, landed by
# DRE-3338): a batch that moved and left no per-card record is a batch nobody
# can undo, and the operator is left diffing lanes to work out which cards this
# drain took. It is also the thing that makes a second dispatch a no-op: a
# record standing on the card refuses the next drain outright.
#
# `groom-drain-refused` is the other half. A drain that refuses a batch and
# says so only in a workflow log is a stall with an alibi, so every refusal the
# drain reaches after reading the card is written onto it, in one line, with
# the reason.
DRAINED_TAG = "groom-drained"
DRAIN_REFUSED_TAG = "groom-drain-refused"

# The note on a card the drain cancels (DRE-4733): `🧺 groom-cancelled:
# <proposal id> — <reason>`, one line, written on the card itself BEFORE its
# lane changes. The reason is the Cancel row's own, which is the line the CEO
# agreed to, so the card carries why it was closed and which batch closed it.
CANCELLED_TAG = "groom-cancelled"

# Every marker this module writes or reads, in one tuple — the set a decline's
# reason is defanged against (DRE-3373). A reason is CEO-written free text that
# `propose` renders back into a Linear comment, and a Linear comment is exactly
# where all of these are read from.
ALL_MARKERS = (PROPOSAL_TAG, *DECISION_TAGS, *REPO_TAGS, DRAINED_TAG,
               DRAIN_REFUSED_TAG, CANCELLED_TAG, console_receipt.TAG)

# The answering paragraph's opener (DRE-3373). A constant because the console
# finds the answer by this string, so a rename here is a rename there.
ANSWER_OPENER = "**Answering your decline of"

# The six outcomes a card can carry in the drain's record. Data, so the render
# and the console mirror one list rather than two spellings of it. `already
# gone` is a card that left the lane after the proposal (DRE-4733): a ROW, and
# never a clause on the summary line the console parses.
DRAIN_OUTCOMES = ("moved", "held back", "added", "cancelled", "refused",
                  "already gone")

# The id a refusal names when the run could not read one at all — no approval,
# and no proposal comment on the card either. Deliberately NOT id-shaped
# (`[0-9a-f]{6,}`), so a reader matching ids cannot mistake it for a batch that
# exists.
NO_BATCH_ID = "no-batch"

# Said in the proposal AND in docs/groomer.md, from one string, because
# somebody will otherwise read cycle assignment as a return to sprint planning.
CYCLE_IS_NOT_SPRINT_PLANNING = (
    "Assigning cards to cycles is not a return to sprint planning. The cycle is "
    "the OKR heartbeat — a reporting rhythm, not a capacity commitment — and it "
    "still reports what moved. What the groomer needs from it is a native "
    "container for an ORDER, which Linear already has and nobody has to build."
)

# Portico is the business priority, and since DRE-3096 that is a TIE-BREAK and
# not the master key: it separates cards of equal priority created on the same
# day, and nothing else. As the first element of the sort key it put months-old
# Portico work at the head of a 200-card Intake and made a card raised Urgent
# this morning wait its turn.
REPO_PRIORITY = ("portico",)
DEFAULT_CAPACITY = 20
DEFAULT_CYCLE_DAYS = 14
NO_REPO = "(no repo label)"

# The default of `--window-days`, which since DRE-4725 excludes and orders
# nothing. It was how far back the batch reached (CEO decision, 2026-09-04:
# "14 days, creation date"); the CEO's decision on DRE-4669 (2026-09-23)
# replaced it with oldest first and nothing hidden. Still accepted by
# `propose` and the CLI, and still written into the proposal as
# `window_days`, so every caller that passes it keeps running.
WINDOW_DAYS = 14

# Linear's own priority numbers. Only these two are lanes: Medium (3) and Low
# (4) are ordinary cards, and reading them as bands would make "High" mean
# nothing again.
URGENT = 1
HIGH = 2

# The bands, in the order they are applied. A unit's band is the whole of its
# rank's first element, so a band is never mixed with another one — the oldest
# card in the pile cannot outrank Urgent however old it is. Everything that is
# neither Urgent nor High is ONE band, worked oldest first (DRE-4725).
BAND_URGENT = 0
BAND_HIGH = 1
BAND_OLDER = 2
BAND_LABELS = {BAND_URGENT: "Urgent", BAND_HIGH: "High"}

# A path cited by more than this many cards is REFERENCE, not ownership: a
# branch-rule banner naming `.github/workflows/linear-sync.yml` sits on nineteen
# live cards and none of them edits it, and treating that as a collision
# serialises the whole batch on a boilerplate line.
#
# Tuned against the live population on 2026-08-29, and deliberately loose. At 5
# it discarded `responses_lib.ts` (8 cards) — a file the Forms review named as a
# REAL collision — so a tight threshold buys a shorter report by hiding the
# findings this exists to make. A false collision costs one ordering constraint;
# a missed one costs a merge conflict.
BOILERPLATE_THRESHOLD = 12


# The operator's switch on the whole lane (DRE-3035), read once at import, and
# since DRE-4141 this is its one reader. The drain and the sweep's age-out were
# the two things that moved a card out of Intake; the age-out is gone — no card
# leaves that lane for being old — so the drain is the exit and this switch is
# what holds it. `propose` is untouched: it writes nothing but a comment, and a
# held pen still wants a batch prepared for the day it opens.
INTAKE_HOLD = intake_controls.hold()


class DrainRefused(RuntimeError):
    """Every refusal the drain reaches AFTER it has read the proposal card.

    Each one carries the batch it is about, because each one is WRITTEN DOWN:
    the drain posts `🧺 groom-drain-refused: <id> — <reason>` before it
    re-raises, so a refusal is on the card the CEO approved on rather than in a
    workflow log nobody opens (DRE-3370). `batch` falls back to NO_BATCH_ID
    when the run could not read an id at all.
    """

    def __init__(self, message: str, *, batch: str | None = None):
        super().__init__(message)
        self.batch = batch or NO_BATCH_ID


class IntakeHeld(DrainRefused):
    """`INTAKE_HOLD` is set, so nothing leaves Intake. Raised BEFORE any write
    and ahead of the approval's own verdict: an operator who closed the pen has
    said "not this week" about every batch, including one approved last week."""


class NotApproved(DrainRefused):
    """The batch has no CEO approval — none was written, the only one was
    written by the pipeline itself, or the newest decision on the card is a
    DECLINE. Raised BEFORE any write: a gate that refuses after moving three
    cards is not a gate."""


class NoProposalRecord(DrainRefused):
    """The approval names a proposal id the card carries no proposal for, so
    there is no list to move (DRE-3338). Raised BEFORE any write: the drain
    moves a written-down batch and never a reconstructed one."""


class AlreadyDrained(DrainRefused):
    """A `groom-drained` record for this batch already stands on the card, so
    this dispatch is the second one and it moves nothing (DRE-3370). The record
    the first drain wrote is the receipt; re-executing an order that has
    already been executed moves cards nobody approved twice."""


class CycleRefused(DrainRefused, ValueError):
    """The record's cycle cannot be honoured: it names none, it spans several
    with no way to say which card is in which, or Linear carries no open cycle
    with that number. A ValueError as well, because it was one before DRE-3370
    made every refusal a written record and callers still catch it that way."""


class ProposalContradiction(RuntimeError):
    """The proposal both proposes a card and says the read could not place it
    (DRE-3544).

    Raised at write time, never repaired: the two lists are made disjoint where
    the batch is built, so an overlap here is a defect upstream of the page and
    the proposal it would write is one a drain must not be allowed to move.
    Failing the run costs a groom pass; posting it puts a card the model
    declined in front of the CEO as work, with "could not rank — needs a
    person" printed as the reason it is being proposed."""


class WillNotClose(RuntimeError):
    """The drain was pointed at a lane it never writes the Planning list into:
    one in `NEVER_WRITES` — it never closes a card — or `CANCEL_TO`, which it
    writes only for an agreed row of the Cancel table (DRE-4733).

    The ONE refusal that writes nothing: it is a bad invocation, caught before
    the card has been read, so there is no batch for a record to name."""


# --------------------------------------------------------------------------- #
# reading the population                                                       #
# --------------------------------------------------------------------------- #

# The lane travels as a VARIABLE, never interpolated into the query text, and
# the query declares $after / selects pageInfo because `gql_paged` refuses one
# that cannot paginate.
POPULATION_QUERY = """query($lane: String!, $after: String) {
  issues(first: 100, after: $after, filter: {state: {name: {eq: $lane}}}) {
    nodes {
      identifier title description createdAt priority
      state { name }
      labels { nodes { name } }
      parent { identifier title }
      project { name }
      cycle { number }
      inverseRelations(first: 20) { nodes {
        type issue { identifier state { name } }
      } }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""

CYCLES_QUERY = """query {
  cycles(first: 50) { nodes { id number startsAt endsAt completedAt } }
}"""

SET_CYCLE = """mutation($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}"""


def read_population(lops, lane: str = "Intake") -> list[dict]:
    """Every card in `lane`, followed to exhaustion."""
    return lops.gql_paged(POPULATION_QUERY, {"lane": lane})


def read_cycles(lops, *, now: str | None = None) -> list[dict]:
    """The cycles a batch may be placed into: the ones that have not started.

    The running cycle is excluded on purpose — dropping a freshly approved batch
    into a cycle that is already half over reports work as belonging to a period
    it could not have been done in.
    """
    now = now or _now()
    out = []
    for node in (lops.gql(CYCLES_QUERY)["cycles"]["nodes"] or []):
        if node.get("completedAt") or (node.get("startsAt") or "") <= now:
            continue
        out.append({"number": node["number"], "id": node["id"],
                    "startsAt": node.get("startsAt"), "endsAt": node.get("endsAt")})
    return sorted(out, key=lambda c: c["number"])


def cycle_ids(lops) -> dict:
    """`{cycle number: id}` for every cycle Linear still carries.

    The DRAIN's reading of the cycles, and deliberately NOT `read_cycles`: that
    one excludes the running cycle because a PROPOSAL must not schedule work
    into a period already half over. A drain is not scheduling — it writes a
    cycle number the CEO already approved, days after the proposal was read,
    and by then the cycle it names has often opened. Filtering it out here
    would refuse every batch that took a weekend to approve.

    A completed cycle is still excluded: writing work into a period that is
    over reports it as having been done then, which is the same lie from the
    other end.
    """
    out = {}
    for node in (lops.gql(CYCLES_QUERY)["cycles"]["nodes"] or []):
        if node.get("completedAt"):
            continue
        try:
            out[int(node["number"])] = node["id"]
        except (KeyError, TypeError, ValueError):
            continue
    return out


def repo_of(card: dict) -> str:
    for label in ((card.get("labels") or {}).get("nodes") or []):
        name = label.get("name") or ""
        if name.startswith("repo:"):
            return name.split(":", 1)[1]
    return NO_REPO


def census(cards: list[dict]) -> dict:
    return dict(sorted(Counter(repo_of(c) for c in cards).items(),
                       key=lambda kv: (-kv[1], kv[0])))


# --------------------------------------------------------------------------- #
# supersession — read, never inferred                                          #
# --------------------------------------------------------------------------- #

# A DECLARATION opens its own line and names its target. The blocker line learned
# this the hard way: a bare substring match over prose read a dependency out of
# the sentence "neither depends on the other" and froze five cards for five days
# (DRE-2670). A mention is not a declaration.
_SUPERSEDED_LINE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\*\*)?\s*superseded\s+by\s*:?\s*(?:\*\*)?\s*(.+?)\s*$",
    re.I | re.M,
)
# A card SAYING it is superseded, near the start of a line — the shape of a
# declaration someone wrote about this card, as opposed to the word appearing
# deep inside a paragraph about something else (a card quoting "none marked
# superseded" is discussing ADRs, not itself).
_MENTIONS_SUPERSESSION = re.compile(r"^.{0,40}?\bsupersed(?:e|es|ed|ing)\b", re.I | re.M)
_CARD_REF = re.compile(r"\b(DRE-\d+)\b")
_PR_URL = re.compile(r"https://github\.com/\S+/pull/\d+")


def superseded_by(description: str | None) -> str | None:
    """The card or merged PR a `Superseded by:` line names, or None.

    None covers both "nothing was declared" and "something was declared and
    named nothing" — the second is reported separately (`unstated_supersessions`)
    rather than guessed at, because a dead recommendation nobody can check is a
    recommendation nobody should act on.
    """
    for line in _SUPERSEDED_LINE.findall(description or ""):
        pr = _PR_URL.search(line)
        if pr:
            return pr.group(0)
        card = _CARD_REF.search(line)
        if card:
            return card.group(1)
    return None


def supersession_gap(description: str | None) -> bool:
    """The card talks about being superseded but names nothing checkable."""
    text = description or ""
    return bool(_MENTIONS_SUPERSESSION.search(text)) and superseded_by(text) is None


# --------------------------------------------------------------------------- #
# collisions — the fact that only exists between two cards                     #
# --------------------------------------------------------------------------- #

_EXTENSIONS = (
    "ts|tsx|js|jsx|mjs|cjs|py|rb|go|java|php|vue|svelte|css|scss|html|md|"
    "json|ya?ml|toml|ini|cfg|sql|sh|tf"
)
# Backticked only: a path in prose is usually a description, a path in code
# ticks is a declaration. Trailing line references (`render.ts:341`) are kept
# out of the name.
_FILE_REF = re.compile(rf"`([A-Za-z0-9_@./-]+\.(?:{_EXTENSIONS}))(?::[\d,\s:-]*)?`")


def file_references(description: str | None) -> set[str]:
    """The files a card says it touches, by BASENAME.

    Basenames because cards cite the same file at different depths — one writes
    `Thread.tsx`, the next `rails/CommentsRail/Thread.tsx`. Comparing full paths
    would miss the collision that costs a merge conflict.
    """
    return {ref.split("/")[-1] for ref in _FILE_REF.findall(description or "")}


def blockers_of(card: dict) -> set[str]:
    """Cards that must come before this one: formal `blocks` relations plus the
    description's own dependency declaration (`blocker_prose` — the ONE anchored
    grammar the sweep and the create seam also read, so no two readers can
    disagree about what counts as a declaration; DRE-2922)."""
    found = set()
    for rel in ((card.get("inverseRelations") or {}).get("nodes") or []):
        if rel.get("type") == "blocks":
            issue = rel.get("issue") or {}
            if (issue.get("state") or {}).get("name") not in ("Done", "Canceled",
                                                              "Duplicate"):
                found.add(issue.get("identifier"))
    found |= set(blocker_prose.blocker_ids(card.get("description")))
    found.discard(card.get("identifier"))
    parent = (card.get("parent") or {}).get("identifier")
    found.discard(parent)                    # an epic never blocks its own child
    return {f for f in found if f}


def collision_report(cards: list[dict], *,
                     threshold: int = BOILERPLATE_THRESHOLD) -> dict:
    """Every pair of cards that names the same file, with a direction.

    Also returns what it could NOT see: the paths it discarded as boilerplate
    (with their counts) and the cards that name no files at all. Five of the
    eight collisions DRE-2649 found are invisible to this method because the
    cards name no files — a coverage gap that is stated is one somebody can
    close, and one that is silent is the whole failure this card exists for.
    """
    refs = {c["identifier"]: file_references(c.get("description")) for c in cards}
    by_id = {c["identifier"]: c for c in cards}
    counts: Counter = Counter()
    for names in refs.values():
        counts.update(names)
    boilerplate = {name: n for name, n in counts.items() if n > threshold}

    pairs = []
    for a, b in itertools.combinations(sorted(refs, key=_card_sort_key), 2):
        # SAME REPO ONLY. A shared basename across two repositories is not a
        # collision — `package.json` in portico and `package.json` in deltasolv
        # are different files that can never conflict. Measured live on
        # 2026-08-29: cross-repo matches on `CLAUDE.md`, `repo-map.json` and
        # `deploy.sh` pulled an agent-bureau card ahead of most of Portico,
        # breaking the one ordering rule the batch has.
        if repo_of(by_id[a]) != repo_of(by_id[b]):
            continue
        shared = (refs[a] & refs[b]) - set(boilerplate)
        if not shared:
            continue
        before, after = _order_of(by_id[a], by_id[b])
        pairs.append({"before": before, "after": after, "files": sorted(shared),
                      "why": _collision_why(by_id[before], by_id[after], shared)})
    return {
        "pairs": pairs,
        "boilerplate": dict(sorted(boilerplate.items())),
        "unreadable": sorted((i for i, names in refs.items() if not names),
                             key=_card_sort_key),
    }


def _order_of(a: dict, b: dict) -> tuple[str, str]:
    """Which of two colliding cards goes first. A recorded relation decides it;
    otherwise the older card, which is arbitrary but explicit — the point is
    that SOME order exists and is written down with its reason."""
    if b["identifier"] in blockers_of(a):
        return b["identifier"], a["identifier"]
    if a["identifier"] in blockers_of(b):
        return a["identifier"], b["identifier"]
    key = (_created(a), _card_sort_key(a["identifier"]))
    other = (_created(b), _card_sort_key(b["identifier"]))
    return ((a["identifier"], b["identifier"]) if key <= other
            else (b["identifier"], a["identifier"]))


def _collision_why(before: dict, after: dict, shared: set[str]) -> str:
    files = ", ".join(sorted(shared))
    if after["identifier"] in blockers_of(before) or \
            before["identifier"] in blockers_of(after):
        return f"both touch {files}; a recorded blocks relation sets the order"
    return f"both touch {files}; older card first, and the order is recorded"


# --------------------------------------------------------------------------- #
# units — the epic is the atom                                                 #
# --------------------------------------------------------------------------- #

def units(cards: list[dict]) -> list[dict]:
    """Cards grouped into the things a cycle is filled with: an epic with all
    of its children present, or a single parentless card.

    Each unit carries the band it is sequenced in and the age it is ordered
    by. The epic is the atom, so both are read across the whole unit: the band
    is the highest priority among the epic and its children — one Urgent child
    pulls its epic's unit into the batch (DRE-3096) — and the age is its OLDEST
    card, `created`, so one new child does not send an old epic to the back of
    the pile (DRE-4725).
    """
    grouped: dict[str, list[dict]] = {}
    for card in sorted(cards, key=lambda c: _card_sort_key(c["identifier"])):
        parent = (card.get("parent") or {}).get("identifier")
        grouped.setdefault(parent or card["identifier"], []).append(card)

    out = []
    for key, members in grouped.items():
        # The first member that HAS a parent, not the first member: when the
        # epic card is itself in the lane it joins its own unit — which is
        # right, it should move with its children — and it is the one card in
        # the group with no parent to read the epic off.
        epic = next(((m.get("parent") or {}).get("identifier") for m in members
                     if (m.get("parent") or {}).get("identifier")), None)
        repos = Counter(repo_of(c) for c in members)
        repo = sorted(repos.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        priority = min((p for p in (_priority(c) for c in members) if p),
                       default=0)
        out.append({
            "key": key,
            "epic": epic,
            "repo": repo,
            "created": min(_created(c) for c in members),
            "priority": priority,
            "band": _band(priority),
            "cards": [c["identifier"] for c in members],
        })
    return sorted(out, key=lambda u: (u["created"], u["key"]))


def _priority(card: dict) -> int:
    """The card's Linear priority, and only the two that are lanes.

    Linear numbers priority 0 none, 1 Urgent, 2 High, 3 Medium, 4 Low. Anything
    that is not Urgent or High reads as 0 here — an unset priority and a Medium
    one get the same treatment, which is the point: only two lanes exist.
    """
    try:
        value = int(card.get("priority") or 0)
    except (TypeError, ValueError):
        return 0
    return value if value in (URGENT, HIGH) else 0


def _band(priority: int) -> int:
    """Which of the three bands a unit sequences in: Urgent, High, or the rest.

    Priority alone. Age orders cards INSIDE a band and never decides which
    band a card is in, so no card is left out of the batch for being old
    (DRE-4725).
    """
    if priority == URGENT:
        return BAND_URGENT
    if priority == HIGH:
        return BAND_HIGH
    return BAND_OLDER


# --------------------------------------------------------------------------- #
# sequencing                                                                   #
# --------------------------------------------------------------------------- #

def _repo_rank(cards: list[dict], priority) -> dict:
    rest = sorted({repo_of(c) for c in cards} - set(priority))
    return {name: i for i, name in enumerate(list(priority) + rest)}


def _break_cycles(keys: list[str], edges: set[tuple[str, str]], rank,
                  broken: list | None = None) -> set[tuple[str, str]]:
    """Drop the back-edges that make the constraint graph cyclic, FIRST.

    Three Portico epics constrain each other in a loop on the live board
    (DRE-2492 ↔ DRE-2628 ↔ DRE-2629, 2026-08-29). Leaving the loop in and
    breaking it only when the sort runs out of ready nodes is what happened
    first: nothing in the tangle was ever "ready", so the sort emptied every
    other repo first and the highest-priority work in the population came out
    at position 118 of 147. A cycle is a planning question — two things that
    each have to go first — and answering it late silently re-prioritises
    everything else.

    Deterministic: nodes and successors are walked in rank order, so the edge
    dropped is always the one that closes the loop against the ranked order.
    """
    adjacency = {k: [] for k in keys}
    for before, after in edges:
        if before in adjacency and after in adjacency:
            adjacency[before].append(after)
    for key in adjacency:
        adjacency[key].sort(key=lambda k: (rank(k), k))

    kept = set(edges)
    state = {k: 0 for k in keys}                     # 0 unseen, 1 on stack, 2 done
    for root in sorted(keys, key=lambda k: (rank(k), k)):
        if state[root]:
            continue
        stack = [(root, iter(adjacency[root]))]
        state[root] = 1
        while stack:
            node, children = stack[-1]
            nxt = next(children, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            if state[nxt] == 1:                      # a back edge: the loop
                kept.discard((node, nxt))
                if broken is not None:
                    broken.append({
                        "dropped": [node, nxt],
                        "why": "each constrains the other; the ranked order wins",
                    })
                continue
            if state[nxt] == 0:
                state[nxt] = 1
                stack.append((nxt, iter(adjacency[nxt])))
    return kept


def _topo(keys: list[str], edges: set[tuple[str, str]], rank,
          broken: list | None = None) -> list[str]:
    """Kahn's algorithm with a priority heap: the constraints decide what is
    POSSIBLE, the rank decides what happens first among the possible.

    Among the possible, a card is ranked by the best rank among ITSELF AND
    EVERYTHING IT MUST PRECEDE (`_pulled_forward`), so the card a constraint
    puts first is pulled forward to where the card it holds would have gone.
    Under oldest first the older card of a colliding pair is already ahead;
    where the two disagree — an Urgent or High card that collides with, or is
    blocked by, an older unprioritised card — the older card goes ahead of it
    rather than the Urgent card waiting behind the whole pile (DRE-4725).

    A cycle in the graph — A must precede B and B must precede A, which happens
    when a collision and a recorded relation disagree — is broken by rank and
    RECORDED. An order that silently drops a constraint is worse than one that
    says which constraint it could not honour.
    """
    edges = _break_cycles(keys, edges, rank, broken)
    incoming = {k: set() for k in keys}
    outgoing = {k: set() for k in keys}
    for before, after in edges:
        if before in incoming and after in incoming and before != after:
            incoming[after].add(before)
            outgoing[before].add(after)
    pull = _pulled_forward(keys, outgoing, rank)
    heap = [(pull[k], rank(k), k) for k in keys if not incoming[k]]
    heapq.heapify(heap)
    order = []
    remaining = set(keys)
    while remaining:
        if not heap:                       # a constraint cycle: break it, loudly
            stuck = min(remaining, key=lambda k: (rank(k), k))
            for before in list(incoming[stuck]):
                incoming[stuck].discard(before)
                outgoing[before].discard(stuck)
                if broken is not None:
                    broken.append({"dropped": [before, stuck],
                                   "why": "mutual constraints; ranked order wins"})
            heapq.heappush(heap, (pull[stuck], rank(stuck), stuck))
            continue
        *_, key = heapq.heappop(heap)
        if key not in remaining:
            continue
        remaining.discard(key)
        order.append(key)
        for nxt in sorted(outgoing[key]):
            incoming[nxt].discard(key)
            if not incoming[nxt] and nxt in remaining:
                heapq.heappush(heap, (pull[nxt], rank(nxt), nxt))
    return order


def _pulled_forward(keys: list[str], outgoing: dict, rank) -> dict:
    """`{key: the best rank among the key and everything after it}`.

    "After it" is every card the constraints make wait on this one,
    transitively: a blocker of a card that collides with an Urgent card is
    pulled forward as far as the Urgent card, which is the only order that
    leaves neither the conflict nor the wait behind. Walked depth-first and
    without recursion; the edges arrive with their cycles already broken, and
    a node met again while still on the walk is skipped rather than trusted.
    """
    best: dict = {}
    for root in sorted(keys, key=lambda k: (rank(k), k)):
        if root in best:
            continue
        on_walk = {root}
        stack = [(root, iter(sorted(outgoing[root])))]
        while stack:
            node, children = stack[-1]
            nxt = next(children, None)
            if nxt is None:
                stack.pop()
                on_walk.discard(node)
                best[node] = min([rank(node)] + [best[c] for c in outgoing[node]
                                                 if c in best])
                continue
            if nxt in best or nxt in on_walk:
                continue
            on_walk.add(nxt)
            stack.append((nxt, iter(sorted(outgoing[nxt]))))
    return best


def sequence(cards: list[dict], *, collisions: dict | None = None,
             repo_priority=REPO_PRIORITY,
             broken: list | None = None) -> list[dict]:
    """The population as ONE order: unit before unit, card before card.

    Urgent first, then High, then everything else — OLDEST creation day first,
    then the repo priority as the tie-break within a day, then the exact
    timestamp, then the identifier (DRE-4725, reversing DRE-3096's newest
    first). A unit's age is its OLDEST card. Subject to the constraints
    throughout: a file collision (the older card before the newer one that
    names the same file) and a `blockedBy` relation are the edges `_topo`
    honours, at unit level and inside a unit, and the key above decides the
    order among what the edges leave possible — pulling the card a constraint
    puts first forward to where the card it holds would have gone.

    Every card comes out with a position and every row is batchable: nothing
    is left out for its age, so `deferred` is always False and kept only so
    the row keeps its shape. The batch is the first `capacity` of this order
    (`propose`), and the ranked read decides neither membership nor order —
    the model's `now` set no longer fills the batch, its `now` order no longer
    reorders it, and there is no "set the model picked" for a constraint to be
    filtered by. What the read still removes is per card, in `propose`: a card
    it declined.
    """
    collisions = collisions if collisions is not None else collision_report(cards)
    by_id = {c["identifier"]: c for c in cards}
    unit_list = units(cards)
    unit_of = {cid: u["key"] for u in unit_list for cid in u["cards"]}
    ranks = _repo_rank(cards, repo_priority)

    constraints = [(p["before"], p["after"]) for p in collisions["pairs"]]
    for card in cards:
        for blocker in blockers_of(card):
            if blocker in by_id:
                constraints.append((blocker, card["identifier"]))

    unit_edges = {(unit_of[b], unit_of[a]) for b, a in constraints
                  if unit_of[b] != unit_of[a]}
    unit_index = {u["key"]: u for u in unit_list}
    batchable = _batchable(unit_list)

    def unit_rank(key):
        # The band first, then the unit's OLDEST day, and the repo only after
        # the day: Portico separates two cards of the same priority created on
        # the same day, and decides nothing else. The day is the granularity
        # the tie-break is defined at, so the timestamp only orders cards the
        # day cannot separate. A unit with no readable creation date is not
        # the oldest in the pile: it goes to the back of its band, the
        # reversible answer.
        unit = unit_index[key]
        day = _day_ordinal(unit["created"]) or float("inf")
        return (unit["band"], day, ranks[unit["repo"]],
                _epoch(unit["created"]), _card_sort_key(key))

    ordered_units = _topo([u["key"] for u in unit_list], unit_edges, unit_rank,
                          broken)

    rows = []
    position = 0
    for key in ordered_units:
        unit = unit_index[key]
        inner_edges = {(b, a) for b, a in constraints
                       if unit_of.get(b) == key and unit_of.get(a) == key}

        # Inside a unit the order is the build order — oldest child first,
        # constraints on top. The band is a property of the unit, so it has
        # nothing left to say here.
        def card_rank(cid):
            return (_created(by_id[cid]), _card_sort_key(cid))

        for cid in _topo(list(unit["cards"]), inner_edges, card_rank, broken):
            position += 1
            rows.append({
                "identifier": cid,
                "title": by_id[cid].get("title") or "",
                "position": position,
                "unit": key,
                "epic": unit["epic"],
                "repo": repo_of(by_id[cid]),
                "project": ((by_id[cid].get("project") or {}) or {}).get("name"),
                "band": unit["band"],
                "deferred": key not in batchable,
            })
    return rows


def _batchable(unit_list: list[dict]) -> set[str]:
    """The units the batch may contain: every one of them (DRE-4725).

    There is no window left to exclude a unit and no judgement that drops
    one: a unit the read ranked `not-now` is in the order like any other,
    because under oldest first "not now" is not the model's call. What keeps
    a unit out of THIS batch is only its place in the order against
    `capacity`.

    The two things that used to pull an old unit in from outside the window —
    a file collision with a batched card, and being a Linear blocker of one —
    still put that unit ahead of what it constrains, and transitively: a card
    that blocks a card that collides with a batched card goes ahead of both.
    That is the order's doing now (`_topo`, `_pulled_forward`), because a
    unit that is always batchable needs no pulling into the set.

    The one exclusion that survives is per CARD, not per unit, and it lives in
    `propose`: a card the read declined comes out of the batch (DRE-3544) and
    the next card in order takes its slot.
    """
    return {u["key"] for u in unit_list}


def cycle_plan(rows: list[dict], cycles: list[dict], capacity: int) -> list[dict]:
    """Fill cycles in sequence order, never splitting a unit.

    A unit larger than the capacity gets a cycle to itself rather than being
    cut in half: eleven children of one Forms epic spread over three cycles is
    one deliverable reported three times.
    """
    slots = _slots(cycles)
    index, used = 0, 0
    placed = {}
    for _, group in itertools.groupby(rows, key=lambda r: r["unit"]):
        members = list(group)
        if used and used + len(members) > capacity:
            index, used = index + 1, 0
        number, cycle_id, projected = slots(index)
        for row in members:
            placed[row["identifier"]] = (number, cycle_id, projected)
        used += len(members)
        if used >= capacity:
            index, used = index + 1, 0
    out = []
    for row in rows:
        number, cycle_id, projected = placed[row["identifier"]]
        out.append({**row, "cycle": number, "cycle_id": cycle_id,
                    "projected": projected})
    return out


def _slots(cycles: list[dict]):
    known = list(cycles)
    last = known[-1]["number"] if known else 0

    def slot(index: int):
        if index < len(known):
            c = known[index]
            return c["number"], c["id"], False
        return last + (index - len(known) + 1), None, True

    return slot


def cycle_days(cycles: list[dict]) -> int:
    for c in cycles:
        try:
            start = datetime.fromisoformat(c["startsAt"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(c["endsAt"].replace("Z", "+00:00"))
        except (AttributeError, KeyError, ValueError):
            continue
        days = (end - start).days
        if days > 0:
            return days
    return DEFAULT_CYCLE_DAYS


# --------------------------------------------------------------------------- #
# the proposal                                                                 #
# --------------------------------------------------------------------------- #

def propose(cards: list[dict], *, cycles: list[dict], capacity: int = DEFAULT_CAPACITY,
            batch_cycles: int = 1, repo_priority=REPO_PRIORITY,
            lane: str = "Intake", now: str | None = None,
            window_days: int = WINDOW_DAYS, judgement=None,
            held_repos=()) -> dict:
    """The whole population, sequenced, with one outcome per card.

    `judgement` is a `groom_judgement.Judgement` (or a bare
    `dict[str, Verdict]`) and `None` is the rules-only path this has always
    taken — `--no-judgement`, kept byte-for-byte so the audit card can run the
    two readings over one population (DRE-3150).

    `window_days` is accepted and changes nothing (DRE-4725): the batch is
    the oldest cards first and nothing is hidden for its age.

    `held_repos` is the slugs the CEO has switched off (DRE-3403). Their cards
    are removed HERE, before anything else reads them, so a held card is never
    ranked, never sequenced, never given a cycle, never counted against
    `capacity` and never half of a collision pair or a pull-forward. What stays
    the whole lane is `population` and the census: a held repo's cards are
    still visible as existing, and only the OFFER shrinks.
    """
    now = now or _now()
    # The whole lane, kept under its own name — every read below this line is
    # of the offer, and the two must not be able to swap by accident.
    in_lane = list(cards)
    holds = held_repo_rows(in_lane, held_repos)
    cards = offered(in_lane, held_repos)
    verdicts = _verdicts_of(judgement)
    # Which cards would go on the Cancel list, and on whose word (DRE-4727).
    # Decided per card here and ACTED ON only for a card the walk below places
    # in the morning's set: a card outside it waits its turn like any other —
    # it is not cancelled early, and it is not shown as dead. So every card is
    # sequenced, and the Cancel list is drawn from the same twenty as the
    # Planning list rather than from the whole lane.
    to_cancel: dict = {}
    unstated = []
    for card in sorted(cards, key=lambda c: _card_sort_key(c["identifier"])):
        target = superseded_by(card.get("description"))
        if target:
            to_cancel[card["identifier"]] = (DEAD_FROM_LINE, target)
            continue
        # The model's call lands in the SAME list as the regex's, with its
        # evidence as the reason beside the other's `Superseded by:` line. A
        # card the description already condemned is not re-judged: the
        # declaration on the card is the stronger of the two, because a person
        # wrote it.
        verdict = verdicts.get(card["identifier"]) if verdicts else None
        if verdict is not None and verdict.outcome == "likely-done":
            to_cancel[card["identifier"]] = (DEAD_FROM_JUDGEMENT, None)
            continue
        if supersession_gap(card.get("description")):
            unstated.append(card["identifier"])

    collisions = collision_report(cards)
    broken: list = []
    ordered = sequence(cards, collisions=collisions, broken=broken,
                       repo_priority=repo_priority)
    # A card the read DECLINED is not in the batch (DRE-3544). The rules put
    # DRE-3020 at position 32 of `f673bfefa340` — under the capacity — and the
    # model had said it could not place it, so the CEO was shown a batch row
    # whose own reason read "could not rank — needs a person" and a drain
    # would have moved it. The removal is HERE, before the cycles are filled,
    # so `outcomes["now"]` and `judgement["unranked"]` are disjoint by
    # construction rather than by where the sequence happened to land: batch 2
    # was clean by one position, which is the difference between a guard and a
    # coincidence (`docs/groomer-judged-batch.md`).
    #
    # Per CARD, not per unit: an epic is one unit for ORDER, and that is not a
    # way into the batch for a card the read refused to place. The declined
    # card is reported as `not-now` with no cycle and no trigger — what is
    # owed is a person — and named in its own section. And the walk goes on
    # without it: the declined card takes no slot, so the next card in order
    # fills it (DRE-4725). It is the ONLY thing the read removes — a `not-now`
    # verdict keeps its card in the order like any other.
    #
    # Except a card its own description condemned: the read never judged it
    # (the declaration a person wrote outranks it, `_mark`), so its answer —
    # or its silence — about that card cannot take it off the Cancel list.
    declined = declined_cards(verdicts, getattr(judgement, "problem", None)) \
        - {cid for cid, (source, _) in to_cancel.items()
           if source == DEAD_FROM_LINE}
    planned = {row["identifier"]: row for row in cycle_plan(
        [r for r in ordered if r["identifier"] not in declined], cycles,
        capacity)}

    batch_numbers = sorted({r["cycle"] for r in planned.values()})[:batch_cycles]
    # The morning's set is the batch cycle(s) — `capacity` cards, Planning
    # plus Cancel together, units never split — and each card in it goes on
    # exactly one of the two lists. Each list is numbered from 1 in the rules'
    # order, so the page reads as two lists and not as one with holes in it.
    now_rows, later_rows, dead = [], [], []
    for row in ordered:
        if row["identifier"] in declined:
            later_rows.append({"identifier": row["identifier"],
                               "title": row["title"], "repo": row["repo"],
                               "reconsidered_in": None, "projected": False,
                               "older_than_window": False})
            continue
        row = planned[row["identifier"]]
        if row["cycle"] in batch_numbers and row["identifier"] in to_cancel:
            source, target = to_cancel[row["identifier"]]
            dead.append({"identifier": row["identifier"],
                         "title": row["title"], "repo": row["repo"],
                         "superseded_by": target, "source": source,
                         "position": len(dead) + 1, "epic": row["epic"],
                         "band": row["band"]})
        elif row["cycle"] in batch_numbers:
            now_rows.append({**{k: row[k] for k in
                                ("identifier", "title", "position", "cycle",
                                 "cycle_id", "unit", "epic", "repo",
                                 "projected", "band")},
                             "position": len(now_rows) + 1})
        else:
            # Outside the batch is `not-now` with the cycle it is projected
            # into, whatever the card's age — nothing is held back unscheduled
            # any more, so `older_than_window` is False on every row.
            later_rows.append({"identifier": row["identifier"],
                               "title": row["title"], "repo": row["repo"],
                               "reconsidered_in": row["cycle"],
                               "projected": row["projected"],
                               "older_than_window": False})

    # The declined rows carry no cycle here either: the sequence is what the
    # console and the audit read, and a row that says `not-now` beside a cycle
    # it was going to be batched in is the same contradiction one layer down.
    # A card on the Cancel list keeps its place in the order and carries no
    # cycle either: it is proposed for cancellation, not scheduled.
    cancelled = {d["identifier"] for d in dead}
    sequence_rows = [{**r, "cycle": None, "cycle_id": None, "projected": False,
                      "outcome": "not-now"} if r["identifier"] in declined
                     else {**planned[r["identifier"]], "cycle": None,
                           "cycle_id": None, "projected": False,
                           "outcome": "dead"} if r["identifier"] in cancelled
                     else {**planned[r["identifier"]],
                           "outcome": ("now" if planned[r["identifier"]]["cycle"]
                                       in batch_numbers else "not-now")}
                     for r in ordered]

    in_batch = {row["identifier"] for row in now_rows}
    proposal = {
        "generated_at": now,
        "lane": lane,
        # The LANE, not the offer: a held repo's cards are still there, and a
        # census that hid them would say the work had gone away.
        "population": len(in_lane),
        "census": census(in_lane),
        # …and what was actually put in front of the reader (DRE-3403).
        "held_repos": holds,
        "offered": len(cards),
        "held_blockers": [row for row in blocked_by_held(cards, in_lane,
                                                         held_repos)
                          if row["identifier"] in in_batch],
        "capacity": capacity,
        "cycle_days": cycle_days(cycles),
        # Accepted, and a receipt of nothing (DRE-4725): no card is held back
        # for its age, so the count is always 0 and there is no line to print.
        # Both keys stay so every reader of the proposal keeps its shape.
        "window_days": window_days,
        "older_than_window": {"days": window_days, "cards": 0, "line": ""},
        "batch": {"cycles": batch_numbers, "cards": len(now_rows)},
        "repo_order": [r for r in _repo_rank(cards, repo_priority)],
        "sequence": sequence_rows,
        "outcomes": {"now": now_rows, "not-now": later_rows, "dead": dead},
        "collisions": collisions,
        "unhonoured_constraints": broken,
        "unstated_supersessions": unstated,
    }
    proposal["deprioritised"] = _deprioritised(proposal)
    proposal["judgement"] = _annotate(proposal, judgement, verdicts,
                                      declined=declined)
    # `proposed ∩ unranked = ∅`, checked on the thing that was actually built
    # rather than trusted from the filter above (DRE-3544).
    assert_disjoint(proposal)
    # LAST, and deliberately after the annotation: `proposal_id` digests the
    # two lists' cards, positions and (Planning's) cycles and NOTHING else, so
    # a reason that reads differently on a re-run cannot retire a CEO approval
    # of the same lists (DRE-3150's contract, DRE-4727's second list; the same
    # sha-binding idea the merge gate uses).
    proposal["id"] = proposal_id(proposal)
    return proposal


# --------------------------------------------------------------------------- #
# the judgement, written onto the rows                                         #
# --------------------------------------------------------------------------- #


def declined_cards(verdicts: dict | None, problem: str | None) -> set:
    """The cards THE READ ITSELF refused to place (DRE-3544).

    The `unranked` cards of a run that ranked something — the five of
    `f673bfefa340`, one of which the rules batched anyway.

    **A run carrying a `problem` declined nothing.** `problem` is set on
    exactly the paths where no card in the population was ranked — no model
    could be chosen, the call never answered, the answer could not be read —
    and every card is `unranked` by default there. The proposal falls back to
    the rules exactly as it did before the read existed, and the page says so
    above the batch. Reading that list as refusals would empty the batch on
    the one path where the groomer most needs to propose something.
    """
    if problem:
        return set()
    return {identifier for identifier, verdict in (verdicts or {}).items()
            if verdict.outcome == "unranked"}


def assert_disjoint(proposal: dict) -> None:
    """`proposed ∩ unranked = ∅`, or refuse the proposal (DRE-3544).

    The two lists say opposite things about a card — *this is in the batch you
    are approving* and *nobody could place this* — and the drain reads the
    first while the CEO reads both. Asserted rather than repaired: the removal
    happens in `propose`, where the batch is made, so anything left here is a
    defect in a later change and dropping the row quietly would hide it.

    Called twice on purpose, at the two moments the batch becomes real: at the
    end of `propose`, and in `proposal_comment` — the one writer of the batch
    table a drain parses, whatever built the dict it is handed.

    Recomputed off the written proposal rather than trusted from the filter in
    `propose`: two derivations of one rule that disagree is exactly the bug
    this refuses.
    """
    block = proposal.get("judgement") or {}
    batch = {row["identifier"] for row in proposal["outcomes"]["now"]}
    # `declined_cards` says why a `problem` run declines nothing. The page for
    # one of those says, above the batch, that every card below is placed by
    # the rules alone.
    unranked = (set() if block.get("problem")
                else set(block.get("unranked") or ()))
    both = sorted(batch & unranked, key=_card_sort_key)
    if both:
        raise ProposalContradiction(
            f"{_plural(len(both), 'card')} would be proposed and reported as "
            f"one the read could not place: {', '.join(both)}")


def _verdicts_of(judgement) -> dict | None:
    """The `{id: Verdict}` map out of whatever `propose` was handed.

    A `groom_judgement.Judgement` carries the receipt beside the verdicts and
    is what the CLI passes; a bare mapping is the contracted `judge()` return,
    so both work and neither needs a wrapper at the call site.
    """
    if judgement is None:
        return None
    return getattr(judgement, "verdicts", judgement) or {}


def _showable(text: str | None) -> bool:
    """Is this fit to put in front of the CEO?

    ONE guard, `planning_escalation.refusal` — the same seam the planner's
    escalation goes through, because this text is written by a model and the
    CEO reads outcomes and risk, never code.
    """
    return bool(text) and planning_escalation.refusal(text) is None


def _rules_reason(outcome: str, row: dict) -> str:
    """Why the RULES put this card where they put it. Plain English, ours."""
    if outcome == "dead":
        return f"superseded by {row.get('superseded_by')}"
    if outcome == "now":
        band = BAND_LABELS.get(row.get("band"))
        opened = f"marked {band}" if band else "oldest first"
        return f"in the batch by the rules — {opened}, position {row['position']}"
    return (f"wanted, and this batch was full — it is reconsidered in cycle "
            f"{row.get('reconsidered_in')}")


def _rules_trigger(row: dict) -> str:
    """What brings a deferred card back. Always present, because "later" with
    no trigger is "no" wearing a softer word."""
    return f"when cycle {row.get('reconsidered_in')} opens"


def _mark(outcome: str, row: dict, verdict, *, withheld: list,
          declined: bool = False) -> dict:
    """The five fields DRE-3150 and DRE-3764 put on every row: reason, trigger,
    evidence, judged, reasons.

    Every one of them that a model wrote passes `_showable` first. A refused
    REASON is replaced with the contract's exact sentence and the card is
    listed in `withheld`; a refused trigger falls back to the rules' own, and
    refused evidence is dropped to None — a Cancel row whose evidence cannot be
    shown is still reported as judged, its reason is the withheld sentence
    (`_cancel_mark`), and the run log holds the text nobody could put on the
    page. A refused LABELLED reason is dropped and the
    card is listed in `withheld` too, and nothing stands in for it: a label
    with a sentence about the guard under it reads as an answer, and the whole
    point of the five is that the reader can tell what was said from what was
    not.

    `declined` is `propose`'s own answer to "did the READ refuse this card"
    (DRE-3544) — the one place that question is decided, so a row taken out of
    the batch and a row given no trigger are the same rows by construction.
    """
    identifier = row.get("identifier")
    # A card its OWN DESCRIPTION condemned was never placed by the read: the
    # declaration a person wrote outranks it, and the reason has to say what
    # superseded it rather than that a model could not rank it.
    if outcome == "dead" and row.get("source") == DEAD_FROM_LINE:
        verdict = None
    judged = verdict is not None and verdict.outcome != "unranked"
    if outcome == "dead":
        return _cancel_mark(row, verdict, judged, withheld=withheld)
    reason = (verdict.reason if verdict is not None
              else _rules_reason(outcome, row))
    if not _showable(reason):
        if verdict is not None and verdict.outcome == "unranked":
            # The unranked sentence is ours and always showable; anything else
            # here is a model's words, refused.
            reason = verdict.reason
        else:
            withheld.append(identifier)
            reason = WITHHELD_REASON

    trigger = None
    # A declined card is out of the batch (DRE-3544) and names NO trigger: it
    # is not a deferral somebody scheduled, and the rules' fallback here would
    # invent one ("when cycle N opens") for a card no cycle is waiting on.
    # `_render_not_now` lists only the rows that carry a trigger, so it is
    # reported once, under "Could not rank — needs a person", where what it is
    # owed — a person — is what the section says.
    if outcome == "not-now" and not declined:
        trigger = verdict.pointer if (judged and verdict.outcome == "not-now") \
            else None
        if trigger is not None and not _showable(trigger):
            withheld.append(identifier)
            trigger = None
        trigger = trigger or _rules_trigger(row)

    # The labelled reasons, and only on a card that is actually IN the batch —
    # a `now` the cap moved to `not-now` is not a card the section renders, so
    # its labels are not shown and not guarded (DRE-3764).
    reasons = {}
    if outcome == "now" and judged:
        for label, text in (verdict.reasons or {}).items():
            if _showable(text):
                reasons[label] = text
            else:
                withheld.append(identifier)

    return {"reason": reason, "trigger": trigger, "evidence": None,
            "judged": judged, "reasons": reasons}


def _cancel_mark(row: dict, verdict, judged: bool, *, withheld: list) -> dict:
    """The marks on a Cancel row, whose reason is ONE line (DRE-4727).

    The line the CEO reads in the Cancel table and the drain later writes onto
    the card when he agrees, so it is the thing that makes the call checkable:
    `superseded by DRE-N` off the description's own line, or the evidence the
    ranked read named — the superseding card, the merged pull request, the
    decision. The model's `reason` field is not it: "the work already
    happened" names nothing anyone can check. Evidence the plain-English guard
    refuses is replaced with the contract's exact sentence and counted in
    `withheld`, so the reason is never empty.
    """
    if row.get("source") == DEAD_FROM_LINE:
        return {"reason": _rules_reason("dead", row), "trigger": None,
                "evidence": None, "judged": False, "reasons": {}}
    evidence = (verdict.pointer
                if judged and verdict.outcome == "likely-done" else None)
    if evidence is not None and not _showable(evidence):
        withheld.append(row.get("identifier"))
        evidence = None
    return {"reason": evidence or WITHHELD_REASON, "trigger": None,
            "evidence": evidence, "judged": judged, "reasons": {}}


def _annotate(proposal: dict, judgement, verdicts: dict | None, *,
              declined: set | frozenset = frozenset()) -> dict:
    """Write the reason, trigger, evidence and judged flag onto every row, and
    return the proposal's `judgement` block.

    One pass over one map, so a card's reason is the same string in
    `sequence`, in `outcomes` and in the rendered proposal — three copies of a
    reason is three chances for them to disagree.
    """
    context: dict = {}
    for row in proposal["outcomes"]["now"]:
        context[row["identifier"]] = ("now", row)
    for row in proposal["outcomes"]["not-now"]:
        context[row["identifier"]] = ("not-now", row)
    for row in proposal["outcomes"]["dead"]:
        context[row["identifier"]] = ("dead", row)

    withheld: list = []
    marks = {
        identifier: _mark(outcome, row,
                          (verdicts or {}).get(identifier), withheld=withheld,
                          declined=identifier in declined)
        for identifier, (outcome, row) in context.items()
    }
    for rows in (proposal["outcomes"]["now"], proposal["outcomes"]["not-now"],
                 proposal["outcomes"]["dead"], proposal["sequence"]):
        for row in rows:
            row.update(marks.get(row["identifier"], {}))
    for row in proposal["outcomes"]["dead"]:
        row.setdefault("source", DEAD_FROM_LINE)
    for row in proposal["sequence"]:
        if row.get("outcome") == "dead":
            row["source"] = next(
                (d.get("source") for d in proposal["outcomes"]["dead"]
                 if d["identifier"] == row["identifier"]), DEAD_FROM_LINE)

    unranked = sorted({identifier for identifier, v in (verdicts or {}).items()
                       if v.outcome == "unranked"}, key=_card_sort_key)
    return {
        "enabled": verdicts is not None,
        "calls": int(getattr(judgement, "calls", 0) or 0),
        "model_asked": getattr(judgement, "asked", None),
        "model_answered": getattr(judgement, "answered", None),
        "receipt": planning_classify.model_receipt(
            getattr(judgement, "asked", None),
            getattr(judgement, "answered", None)),
        "pack": getattr(judgement, "pack", None) or _EMPTY_PACK(),
        "unranked": unranked,
        "withheld": sorted(dict.fromkeys(withheld), key=_card_sort_key),
        "problem": getattr(judgement, "problem", None),
        # What the one call cost (DRE-3259). `output_budget` is the `max_tokens`
        # it was made with — 0 under `--no-judgement` and whenever no call was
        # made — and `truncated` is the ANSWER being cut at that budget. NOT
        # `pack["truncated"]`, which is the list of context-pack sections that
        # were capped: two facts, two types, and neither is read for the other.
        "output_budget": int(getattr(judgement, "output_budget", 0) or 0),
        "truncated": bool(getattr(judgement, "truncated", False)),
        # What the read DID with the population (DRE-3331): how many cards it
        # ranked, and why every other one is unranked — the model said so, the
        # answer never reached it, the line was garbled, the ceiling dropped
        # it. `unranked` above lists the cards; this says which of those four
        # facts each count is, because the per-card reason reads the same for
        # all of them. `continuations` is how many times the CLI carried the
        # answer into a fresh request; the pieces were joined before parsing.
        "ranked": int(getattr(judgement, "ranked", 0) or 0),
        "accounting": dict(getattr(judgement, "accounting", None) or {}),
        "continuations": int(getattr(judgement, "continuations", 0) or 0),
    }


def _EMPTY_PACK() -> dict:
    """The pack summary of a run that read no pack — the same keys on both
    paths, so the sibling cards parse one shape, and every section UNKNOWN.

    Not zeros: a run holding no pack knows nothing about what is in flight, and
    a zero would say it asked and found nothing (DRE-3329).
    """
    out = {name: None for name in groom_context.SECTIONS}
    out["truncated"] = []
    out["unread"] = sorted(groom_context.SECTIONS)
    return out


def _deprioritised(proposal: dict) -> list[dict]:
    """Which repos are waiting, and roughly how long — derived from the
    sequence, not asserted by hand. Portico first means agent-bureau and
    bureau-pipeline wait, and that is said out loud rather than discovered by
    someone expecting their card to move."""
    in_batch = {row["repo"] for row in proposal["outcomes"]["now"]}
    first_cycle = min(proposal["batch"]["cycles"], default=0)
    days = proposal["cycle_days"]
    rows = []
    waiting: dict[str, list[int]] = {}
    for row in proposal["outcomes"]["not-now"]:
        # A card the read declined has no cycle to wait for — it is reported
        # under "Could not rank", not as a repo that waits.
        if row["repo"] in in_batch or row["reconsidered_in"] is None:
            continue
        waiting.setdefault(row["repo"], []).append(row["reconsidered_in"])
    for repo, numbers in waiting.items():
        starts = min(numbers)
        rows.append({
            "repo": repo,
            "cards": len(numbers),
            "first_cycle": starts,
            "weeks": round((starts - first_cycle) * days / 7),
        })
    return sorted(rows, key=lambda r: (-r["cards"], r["repo"]))


def proposal_id(proposal: dict) -> str:
    """A digest of BOTH LISTS — their cards, their order and the batch's cycles.

    The id is what an approval names, so an approval binds to a batch the way a
    critic verdict binds to a head sha: re-run the groomer after the population
    moves and the id changes, which retires the old approval instead of letting
    it authorise a batch nobody read.

    Since DRE-4727 an approval also cancels the Cancel list, so the list is in
    the digest — identifier and position, never the reason, for the same
    reason no `Why` cell ever was: a reason that reads differently on a re-run
    must not retire an approval of the same lists. Written only when there is
    a Cancel list, so a proposal with none keeps the id it always had.
    """
    body = {
        "lane": proposal["lane"],
        "batch": [[r["identifier"], r["position"], r["cycle"]]
                  for r in sorted(proposal["outcomes"]["now"],
                                  key=lambda r: r["position"])],
    }
    cancel = [[r["identifier"], r.get("position")]
              for r in sorted(proposal["outcomes"].get("dead") or [],
                              key=lambda r: r.get("position") or 0)]
    if cancel:
        body["cancel"] = cancel
    payload = json.dumps(body, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def decision_comment(tag: str, pid: str, *, card: str | None = None,
                     reason: str | None = None) -> str:
    """ONE writer for the whole decision vocabulary (DRE-3370).

    `🧺 <tag>: <id>[ DRE-N][ — <reason>]`. The console writes these, the CEO
    writes them by hand, and `decision_match` reads them back — so the shape
    lives here once rather than in a writer and a reader that drift.
    """
    line = f"{MARK} {tag}: {pid}"
    if card:
        line += f" {card}"
    if reason:
        line += f" — {reason}"
    return line


def approval_comment(pid: str) -> str:
    return decision_comment(APPROVAL_TAG, pid)


def proposal_comment(proposal: dict) -> str:
    # The last gate before the batch table the drain parses exists as text
    # (DRE-3544). Every posting path comes through here.
    assert_disjoint(proposal)
    return (f"{MARK} {PROPOSAL_TAG}: {proposal['id']}\n\n"
            + render_proposal(proposal))


# The same anchoring as the approval line below, for the same reason: a comment
# that mentions the marker mid-sentence is prose ABOUT a proposal, not one.
_PROPOSAL_LINE = re.compile(rf"^\s*(?:{MARK}\s*)?{PROPOSAL_TAG}\s*:\s*([0-9a-f]{{6,}})")


def already_proposed(proposal: dict, records: list[dict]) -> bool:
    """Is THIS batch already proposed on the card?"""
    pid = proposal["id"]
    for record in records:
        match = _PROPOSAL_LINE.match((record.get("body") or "").strip())
        if match and match.group(1) == pid:
            return True
    return False


def post_proposal(lops, card: str, proposal: dict,
                  records: list[dict] | None = None) -> bool:
    """Post the proposal to `card` unless the same batch is already there.

    A retried `propose` must write nothing (vendor boundary Q3), and `--post` is
    how the tool is actually run — `docs/groomer.md` documents that command and
    `groomer.yml` passes `--post` whenever the `card` input is set. Linear's
    `commentCreate` carries no idempotency key, so without this read a
    re-dispatch after a crash (what `self-medic.yml` retries) posts a second
    copy of the same proposal and the thread collects one per retry.

    The proposal id is a digest of the batch's own contents, so "already
    proposed" is decidable from the thread — and a population that MOVED
    produces a different id and posts, because a retry that writes nothing must
    not become a groomer that cannot say anything new. The read is
    `comment_records`' last 50 comments; a proposal that has scrolled out of it
    is posted again. The DRAIN reads the whole thread instead (DRE-3370) — it
    has to, because the approval is the oldest of a card's decisions — and the
    two differ on purpose: a duplicate proposal costs a comment, while a missed
    approval costs a batch.

    `records` is that same read, already in the caller's hand: `main` reads the
    thread once to find the decline this proposal answers (DRE-3373) and passes
    it here rather than asking Linear for the same fifty comments twice. Absent,
    the read happens here exactly as it always did — the idempotence rule is
    untouched either way.

    AND NOT AT ALL FOR AN EMPTY BATCH (DRE-3712). A proposal of nothing asks
    for a decision nobody has to make, and the console reads the marker rather
    than the page: `🧺 groom-proposal: 2b10ecfb36f6` — 0 of 0 cards, posted on
    2026-09-04 while Intake was empty — sat in the CEO's Green Light as
    "waiting 196.9 h" until he asked about it eight days later (DRE-3708, whose
    console half stops SHOWING such a row). The refusal lives here because this
    is the one writer of the marker, so it covers the CLI, the workflow and
    every retry. The lane is still sequenced, still written to `--out` and
    still printed — an empty Intake is a fact worth reporting, just not a
    decision worth queueing.

    Empty means NEITHER list (DRE-4727): a morning with nothing for Planning
    and one card to cancel still asks the CEO something, so it is posted.
    """
    if not proposal["batch"]["cards"] and not proposal["outcomes"]["dead"]:
        print(f"the proposal is empty — no proposal posted to {card} "
              "(a proposal of 0 cards is not a decision)")
        return False
    if records is None:
        records = lops.comment_records(card)
    if already_proposed(proposal, records):
        print(f"proposal {proposal['id']} is already on {card} — not posting again")
        return False
    lops.cmd_comment(card, proposal_comment(proposal))
    return True


# --------------------------------------------------------------------------- #
# answering the last decline (DRE-3373)                                        #
# --------------------------------------------------------------------------- #
#
# DRE-3370 gave the CEO a way to say *no, because…*. The drain honours it and
# then the reason goes nowhere: the next `propose` posts a fresh twenty-card
# page that reads exactly like the one that was turned down, with no sign
# anybody read the objection, and the CEO is left diffing two proposal comments
# by eye to find out whether the thing they complained about was fixed.
#
# So the proposal opens by answering it — the reason quoted back verbatim, and
# then what changed in this batch relative to the declined one, BY ID and
# computed from the two batch lists. Never asked of a model: a sentence a model
# wrote about a difference it did not compute can be wrong in the one place the
# CEO is deciding, and it would be wrong in the CEO's own words.

# Broad on purpose, exactly as `sanitize_untrusted.SENTINEL_RE` is broad: an
# exact match is a bypass (a different id shape, extra spacing, no emoji still
# READS as a marker), and a false positive costs a visible, harmless prefix.
_MARKER_SHAPED = re.compile(
    rf"(?:{MARK}\s*)?(?:{'|'.join(re.escape(t) for t in ALL_MARKERS)})\s*:")


def defang_reason(reason: str | None) -> tuple[str, int]:
    """The reason as it is safe to render, and how many lines were defanged.

    Card text is data (`standards/untrusted-content.md`): the reason is quoted,
    never obeyed. A line inside it shaped like one of this module's own markers
    is prefixed with `[defanged] ` rather than dropped, so a reviewer still sees
    the attempt — and per the standard a `[defanged]` line is itself the
    strongest signal the text is hostile.

    Nothing downstream reads a marker from the middle of a comment (every
    reader here anchors at the start of one), so this is depth rather than the
    only wall. It is worth having anyway: the console and any later reader of
    this page are not bound by that anchoring, and the reason is the one string
    on the page written by somebody the pipeline does not authenticate.
    """
    normalized = (reason or "").replace("\r\n", "\n").replace("\r", "\n")
    lines, defanged = [], 0
    for line in normalized.split("\n"):
        if _MARKER_SHAPED.search(line):
            lines.append(sanitize_untrusted.DEFANG_PREFIX + line)
            defanged += 1
        else:
            lines.append(line)
    return "\n".join(lines), defanged


def decline_to_answer(records: list[dict]) -> dict | None:
    """The decline this run's proposal owes an answer to — `{"id", "reason"}`.

    Four conditions, and each one exists because its absence puts a wrong
    sentence at the top of the page:

      1. **Somebody other than the pipeline wrote it.** The authorship gate the
         whole vocabulary is read under (DRE-2721, DRE-3370) — a marker the
         proposer can write is a credential the proposer can mint, and this one
         would put the proposer's own words in the CEO's mouth.
      2. **It carries the reason the marker requires.** `read_decisions` reads a
         reasonless decline as absent and this reader agrees with it: there is
         nothing to quote.
      3. **It is NEWER than the last `groom-proposal:` on the card.** That
         proposal already answered it. Answering it again puts a complaint the
         CEO made two batches ago at the top of this one.
      4. **The card carries no `groom-approved:` naming that batch.** A decline
         the CEO settled with a yes is not an open argument. This is the card's
         own rule read literally, and it is the quiet direction: at worst a
         proposal renders as it did before this existed, where the other
         reading risks re-opening an argument in the CEO's name.

    The NEWEST decline that clears all four is the one answered — the same
    "last word wins" the approval has always used.
    """
    newest_proposal = -1
    declines: list[tuple[int, str, str]] = []
    approved: set[str] = set()

    for index, record in enumerate(records):
        body = record.get("body") or ""
        if _PROPOSAL_LINE.match(body.strip()):
            newest_proposal = index
            continue
        if not _decides(record):
            continue
        approval = decision_match(APPROVAL_TAG, body)
        if approval:
            approved.add(approval.group(1))
            continue
        marker = decision_match(DECLINE_TAG, body)
        if not marker:
            continue
        reason = _REASON_TAIL.match(marker.group(2))
        if reason:
            declines.append((index, marker.group(1), reason.group(1)))

    for index, pid, reason in reversed(declines):
        if index > newest_proposal and pid not in approved:
            return {"id": pid, "reason": reason}
    return None


def answer_decline(proposal: dict, records: list[dict]) -> dict | None:
    """Attach this proposal's answer to the CEO's last open decline, or not.

    Writes `proposal["answering"]` and returns it. With nothing to answer it
    returns None and leaves the proposal object untouched — which is what makes
    "a proposal with no decline behind it renders byte for byte as today" a
    property of the data rather than a promise about the renderer.

    The proposal id is a digest of the BATCH and is computed in `propose`, so a
    paragraph about a PREVIOUS batch cannot move it and cannot retire a CEO
    approval — the same property the render has always had.
    """
    decline = decline_to_answer(records)
    if decline is None:
        return None
    reason, defanged = defang_reason(decline["reason"])
    # The declined batch, read off its own proposal comment — the list the CEO
    # was looking at when they declined, not a rebuild of it.
    declined = proposal_record(decline["id"], records)
    before = {row["identifier"] for row in (declined or {}).get("batch", [])}
    now = {row["identifier"] for row in proposal["outcomes"]["now"]}
    answering = {
        "id": decline["id"],
        "reason": reason,
        "defanged": defanged,
        # False when the declined proposal has scrolled out of the read. The
        # page then says so instead of rendering an empty diff, which would be
        # indistinguishable from "nothing changed" (console-honesty rule 2).
        "batch_read": declined is not None,
        "cards_in": sorted(now - before, key=_card_sort_key) if declined else [],
        "cards_out": sorted(before - now, key=_card_sort_key) if declined else [],
    }
    proposal["answering"] = answering
    print(f"groomer: this proposal answers the decline of batch "
          f"{decline['id']} — {len(answering['cards_in'])} card(s) in, "
          f"{len(answering['cards_out'])} card(s) out", file=sys.stderr)
    if not answering["batch_read"]:
        print(f"groomer: batch {decline['id']} is on no proposal comment in the "
              f"thread that was read — the answer names no cards in or out",
              file=sys.stderr)
    if defanged:
        # The COUNT only, never the line. Echoing hostile content into a run log
        # is the amplification `sanitize_untrusted` exists to prevent.
        print(f"groomer: the declined reason carried {defanged} marker-shaped "
              f"line(s) — defanged before rendering, and never obeyed",
              file=sys.stderr)
    return answering


def _render_answer(answering: dict) -> list:
    """The block that opens a proposal following a decline.

    Two paragraphs, and the blank line between them is load-bearing: the reason
    is rendered VERBATIM, so it may end without punctuation — a CEO writes
    `two of these cards edit Thread.tsx` — and running the next sentence onto
    the end of it reads as one mangled sentence in the CEO's own words. The
    alternative is inventing a full stop inside quoted text, which is the one
    thing "verbatim" forbids.
    """
    return [f"{ANSWER_OPENER} `{answering['id']}`:** {answering['reason']}",
            "",
            _change_sentence(answering),
            ""]


def _change_sentence(answering: dict) -> str:
    """What changed in this batch relative to the declined one, by id.

    One grammar for all four shapes — cards in, cards out, both, neither — so
    "nothing changed" is a sentence the page says out loud rather than a
    sentence it omits.
    """
    if not answering["batch_read"]:
        return ("That batch's own proposal comment is not in the thread this "
                "run read, so the difference between the two is not computed "
                "here.")
    return (f"Relative to that batch: {_side(answering['cards_in'], 'in')} "
            f"and {_side(answering['cards_out'], 'out')}.")


def _side(identifiers: list[str], word: str) -> str:
    if not identifiers:
        return f"nothing {word}"
    return (f"{_plural(len(identifiers), 'card')} {word} "
            f"({', '.join(identifiers)})")


# --------------------------------------------------------------------------- #
# the proposal, as the CEO reads it                                            #
# --------------------------------------------------------------------------- #

def cycles_named(proposal: dict) -> str:
    """The cycles this batch covers, as the page names them — `12`, `12, 13`,
    or the empty string when the batch has no cycle because it has no cards."""
    return ", ".join(str(n) for n in proposal["batch"]["cycles"])


def title_line(proposal: dict) -> str:
    """The proposal's first line, well-formed for every input (DRE-3712).

    The console keeps the other copy of this pattern
    (`console/backend/groom_proposal.py` in agent-bureau) and reads the cycle
    off this line to title the decision it shows the CEO. A batch with no cycle
    used to render `— cycle ` and nothing after it, and the reader's `\\s*`
    walked across the blank line onto the paragraph below: the Green Light row
    for the 2026-09-04 proposal read *"0 cards for cycle 0 cards of 0 in
    Intake…"* for eight days (DRE-3708).

    So the clause is written only when there is a cycle to name. A trailing
    `— cycle` with nothing behind it is the whole of the bug, and the two
    copies of the pattern change together.
    """
    cycles = cycles_named(proposal)
    head = f"# Groom proposal `{proposal['id']}`"
    return f"{head} — cycle {cycles}" if cycles else head


def render_proposal(proposal: dict) -> str:
    batch = proposal["outcomes"]["now"]
    cancel = proposal["outcomes"]["dead"]
    cycles = cycles_named(proposal)
    w = []
    add = w.append
    add(title_line(proposal))
    add("")
    # The CEO's last open decline, answered before anything else on the page
    # (DRE-3373). Absent unless `answer_decline` found one, so a proposal with
    # no decline behind it renders byte for byte as it did before that card.
    if proposal.get("answering"):
        w.extend(_render_answer(proposal["answering"]))
    # The same clause one paragraph down, omitted on the same condition — it is
    # what the runaway title actually quoted, and "proposed for cycle ," reads
    # as broken to the person deciding even when nothing parses it.
    proposed = "are proposed" + (f" for cycle {cycles}" if cycles else "")
    # Both lists, named in the sentence both readers take the lane from
    # (DRE-4727): `<N> cards of <M> in <lane> are proposed` stays the opening
    # `_LANE_LINE` and the console's copy of it read. Nothing to split, nothing
    # said — the empty page reads as it always did.
    total = len(batch) + len(cancel)
    split = (f" Of those, {len(batch)} for Planning and {len(cancel)} for "
             f"Cancel." if total else "")
    add(f"{total} cards of {proposal['population']} in {proposal['lane']} "
        f"{proposed}, in the order below.{split} Nothing moves "
        f"until you approve it.")
    add("")
    # One line, before anything else, saying what did the ranking and what it
    # read — including the two things a page that stayed silent would let pass
    # for a model's opinion: a reason the guard withheld, and an answer the
    # budget cut short (DRE-3152).
    add(_receipt_line(proposal))
    add("")
    add("**To approve:** comment `" + approval_comment(proposal["id"])
        + "` on this card. Approval moves the Planning list to Planning and "
          "the Cancel list to Canceled. Anything else — including a comment "
          "that mentions the marker — leaves both lists where they are.")
    add("")
    # The rest of the vocabulary, at the one place the CEO is deciding
    # (DRE-3370). A marker nobody is told about is a marker nobody writes.
    add(f"**To say more than yes:** `{MARK} {DECLINE_TAG}: {proposal['id']} — "
        f"<reason>` declines the batch (the reason is required); "
        f"`{MARK} {EXCLUDE_TAG}: {proposal['id']} DRE-N` keeps a card on "
        f"either list in Intake; "
        f"`{MARK} {ADD_TAG}: {proposal['id']} DRE-N` pulls one in, after the "
        f"batch. One comment each, and the newest one about a card wins.")
    add("")
    add("## The population")
    add("")
    add("| Repo | Cards |")
    add("| -- | -- |")
    for repo, count in proposal["census"].items():
        add(f"| {repo} | {count} |")
    add("")
    add("## The batch, in order")
    add("")
    add(f"The Planning list — approving moves these cards to Planning. "
        f"Urgent first, then High, then everything else — oldest first, "
        f"whatever repo it is in, and no card is left out for its age. Repo "
        f"order ("
        + " → ".join(proposal.get("repo_order") or [])
        + ") breaks a tie between cards of equal priority created on the same "
          "day, and decides nothing else. An epic and its children are one "
          "unit — unless a collision or a recorded blocks relation says "
          "otherwise, in which case the constraint wins.")
    add("")
    # `Why` is the row's own `reason` — the model's one line on a judged row,
    # the rule's on a rules-only one. Read, never recomputed: the same string
    # is in the JSON the console and the audit read.
    add("| # | Card | Pri | Repo | Epic | Title | Why |")
    add("| -- | -- | -- | -- | -- | -- | -- |")
    for row in sorted(batch, key=lambda r: r["position"]):
        add(f"| {row['position']} | {row['identifier']} | "
            f"{BAND_LABELS.get(row.get('band'), '—')} | {row['repo']} | "
            f"{row['epic'] or '—'} | {_trim(row['title'])} | "
            f"{_cell(row.get('reason'), 90)} |")
    add("")
    # The same reasons, in full, in their own section — the table above is
    # unchanged, because the console and the drain both read it back
    # (DRE-3764).
    w.extend(_render_batch_reasons(proposal))
    # The other half of the morning's set, and absent when it is empty
    # (DRE-4727) — the console's reader takes no section to mean no
    # cancellation, which is what most mornings are.
    w.extend(_render_cancel(proposal))
    # Only when a judgement ran. `--no-judgement` renders exactly what it
    # rendered before this card, so the audit (DRE-3151) compares two readings
    # of one population rather than two documents.
    w.extend(_render_judgement(proposal))
    add("## Collisions, and what the order does about them")
    add("")
    pairs = proposal["collisions"]["pairs"]
    if pairs:
        for pair in pairs:
            add(f"- {pair['before']} before {pair['after']} — {pair['why']}")
    else:
        add("- None found in this population.")
    # A card the hold took the blocker out from under. Named, never dropped —
    # the dependency gate holds it later (DRE-3403).
    for row in proposal.get("held_blockers") or []:
        add(f"- {row['identifier']} — blocked by a held repo: {row['blocked_by']} "
            f"is in {row['repo']}, which you switched off. It stays in the "
            f"batch, and the dependency gate holds it until that card is done.")
    add("")
    unreadable = proposal["collisions"]["unreadable"]
    if unreadable:
        add(f"Collision cover: {len(unreadable)} card(s) name no file, so a "
            f"collision involving one of them is invisible to this read — "
            + ", ".join(unreadable[:20])
            + ("…" if len(unreadable) > 20 else "") + ".")
        add("")
    if proposal["collisions"]["boilerplate"]:
        cited = ", ".join(f"{name} ({n} cards)" for name, n
                          in proposal["collisions"]["boilerplate"].items())
        add(f"Read as reference rather than ownership: {cited}.")
        add("")
    if proposal.get("unhonoured_constraints"):
        add(f"{len(proposal['unhonoured_constraints'])} constraint(s) point both "
            f"ways and could not all be honoured — the ranked order won, and "
            f"each one is in the JSON. Two cards that each have to go first is a "
            f"planning question, not an ordering one.")
        add("")
    add("## What waits, and roughly how long")
    add("")
    if proposal["deprioritised"]:
        for row in proposal["deprioritised"]:
            add(f"- **{row['repo']}** — {_plural(row['cards'], 'card')}, first "
                f"one in cycle {row['first_cycle']}, roughly "
                f"{_plural(row['weeks'], 'week')} out.")
    else:
        add("- Nothing: every repo has work in this batch.")
    add("")
    later = proposal["outcomes"]["not-now"]
    scheduled = [r for r in later if r["reconsidered_in"] is not None]
    add(f"{len(later)} cards are **not now** — wanted, deliberately not this "
        f"batch. That is 'later', and it is not 'no'."
        + (f" {len(scheduled)} of them carry the cycle they are reconsidered "
           f"in." if scheduled else ""))
    add("")
    w.extend(_render_not_now(proposal))
    if proposal["unstated_supersessions"]:
        add("Named nothing: "
            + ", ".join(proposal["unstated_supersessions"])
            + " say they are superseded without naming what replaced them, so "
              "they are sequenced normally rather than proposed for Cancel.")
        add("")
    w.extend(_render_unranked(proposal))
    w.extend(_render_held(proposal))
    add("## On cycles")
    add("")
    add(CYCLE_IS_NOT_SPRINT_PLANNING)
    add("")
    return "\n".join(w)


def _render_batch_reasons(proposal: dict) -> list:
    """Why each card is in the batch, in full and in labelled form (DRE-3764).

    The batch table carries one `Why`, cut at ninety characters to fit a
    column, and the CEO opening a row in the console wants the rest of it: why
    now, what it is worth, what it costs, what happens if it waits, what it
    waits on. So the same strings are written again here, uncut, one block per
    batch card, in the order the batch is in.

    Its own section, AFTER the table, on purpose. The table is read back by two
    parsers — the drain's (`parse_proposal_comment`) and the console's
    (`groom_proposal._BATCH_ROW`) — and a sixth column would have moved both;
    a section after it moves neither.

    A grammar, not a paragraph: `### <card id>`, then `- **<Label>:** <line>`.
    The console parses it, so it is fixed in tests rather than left to read
    well. Nothing is written for a label the read did not answer or the
    plain-English guard refused — an absent line is the honest rendering of an
    absent reason, and `withheld` already says how many there were.

    Absent on the `--no-judgement` path, like every other thing the ranked read
    writes: the rules place a card by priority and age and have no fifth
    labelled thing to say about it, and the audit (DRE-3151) compares two
    readings of one population rather than two documents.
    """
    block = proposal.get("judgement") or {}
    batch = sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])
    if not block.get("enabled") or not batch:
        return []
    w = [BATCH_REASONS_HEADING, ""]
    w.append("The same reasons as the table above, in full — this is what the "
             "console shows when you open a row. A line the read did not give "
             "is left out rather than filled in.")
    w.append("")
    for row in batch:
        w.append(f"### {row['identifier']}")
        w.append(f"- **Why:** {_line(row.get('reason'))}")
        for label, text in (row.get("reasons") or {}).items():
            w.append(f"- **{groom_judgement.REASON_LABELS[label]}:** "
                     f"{_line(text)}")
        w.append("")
    return w


def _render_judgement(proposal: dict) -> list:
    """The ranked read, as the CEO reads it — or nothing at all.

    Nothing at all is the `--no-judgement` path, and it is load-bearing: the
    audit card runs the two readings over one population and a section that
    rendered on both would make them differ on the page for no reason.
    """
    block = proposal.get("judgement") or {}
    if not block.get("enabled"):
        return []
    w = ["## What the ranked read said", ""]
    if block.get("problem"):
        w += [block["problem"], "",
              "Every card below is placed by the rules alone, exactly as it "
              "would have been before this read existed.", ""]
    # The batch's own Why is in the table above and every trigger is in "Not
    # now — and when to come back", so what is left to say here is why the
    # cards it did NOT batch are not in the batch: one line per card, and the
    # only place a deferred card's reason appears.
    later = proposal["outcomes"]["not-now"]
    if later:
        w += ["What it did not put in the batch, and why:", ""]
        w.append("| Card | Why |")
        w.append("| -- | -- |")
        for row in later[:20]:
            w.append(f"| {row['identifier']} | {_cell(row.get('reason'), 90)} |")
        if len(later) > 20:
            w.append(f"| … | and {len(later) - 20} more, each with its reason "
                     f"in the proposal JSON |")
        w.append("")
    if block.get("withheld"):
        w.append(f"{_plural(len(block['withheld']), 'card')} had a reason "
                 f"written in technical terms; it is in the run log rather "
                 f"than on this page — "
                 + ", ".join(block["withheld"][:20]) + ".")
        w.append("")
    return w


def _pack_count(pack: dict, name: str, noun: str) -> str:
    """One context section as the CEO reads it: a count, or UNKNOWN.

    A section the run could not read has no count, and the number it would
    otherwise default to is `0` — which reads as "the fleet shipped nothing
    this fortnight" on a night it merged several pull requests. That is the
    failure `standards/console-honesty.md` rule 2 exists to prevent: a
    plausible-looking default is indistinguishable from a real answer, and it
    also fed the ranking as if it were one (DRE-3329). A section that WAS read
    and held nothing still says `0` — the two facts get visibly different
    renderings.
    """
    if name in set(pack.get("unread") or ()) or pack.get(name) is None:
        return f"UNKNOWN {noun}s (could not be read this run)"
    return _plural(int(pack.get(name) or 0), noun)


def _receipt_line(proposal: dict) -> str:
    """What ranked this batch, over how much, against what — in one line.

    The line the CEO reads before anything else, and the only place three
    facts about the read are said at all: that there WAS one (or was not), how
    many reasons the plain-English guard withheld, and whether the answer came
    back cut. A page that stayed silent about a cut would show a card as one
    the model declined to rank, when in fact the model never got to it.
    """
    block = proposal.get("judgement") or {}
    if not block.get("enabled"):
        return ("Ranked by the rules only (judgement off) — priority, "
                "creation date, file collisions and blocker relations, and "
                "nothing about what we are already doing.")
    pack = block.get("pack") or {}
    line = (f"Ranked by {block.get('receipt')} in "
            f"{_plural(int(block.get('calls') or 0), 'call')} over "
            f"{_plural(proposal['population'], 'card')}, against "
            f"{_pack_count(pack, 'epics_in_progress', 'epic')} in "
            f"flight, {_pack_count(pack, 'merged_prs', 'merged PR')}"
            f" and {_pack_count(pack, 'closed_cards', 'closed card')}")
    # `judgement.truncated` is the ANSWER being cut at the budget — never
    # `pack['truncated']`, which is the list of context sections that were
    # capped. A proposal written before DRE-3259 carries neither key, and this
    # line then says nothing about a budget and invents no number.
    if block.get("truncated"):
        cut = "— the answer was cut short"
        if block.get("output_budget"):
            cut += f" at {block['output_budget']} tokens"
        line += (f" {cut}; "
                 f"{_plural(len(block.get('unranked') or []), 'card')} could "
                 f"not be ranked for that reason")
    # The count (DRE-3331), read only when the proposal carries it: a run that
    # says `answered` says how many cards that answer ranked, and where the
    # rest went. A proposal written before this key invents no number.
    if "ranked" in block:
        line += (f" — {block['ranked']} of "
                 f"{_plural(proposal['population'], 'card')} ranked")
        counts = block.get("accounting") or {}
        rest = [f"{counts[key]} {label}" for key, label in (
            ("declined", "the model declined"),
            ("omitted", "never reached"),
            ("garbled", "unreadable"),
            ("ceiling", "over the ceiling"),
        ) if counts.get(key)]
        if rest:
            line += " (" + ", ".join(rest) + ")"
    line += "."
    if block.get("continuations"):
        line += (f" The answer came back in "
                 f"{_plural(int(block['continuations']) + 1, 'piece')} and "
                 f"was joined before it was read.")
    if block.get("withheld"):
        line += (f" {_plural(len(block['withheld']), 'reason')} written in "
                 f"technical terms were withheld and are in the run log.")
    return line


def _render_not_now(proposal: dict) -> list:
    """Every deferred card, grouped by the thing that brings it back.

    Grouped because four cards waiting on one card finishing is ONE fact, and
    printing it four times is how a CEO learns to skim the section. A row with
    no trigger at all — a card the read declined — is not listed here: "Could
    not rank" reports it, and inventing a trigger for a card nothing scheduled
    would say the groomer had made a plan for it.
    """
    later = proposal["outcomes"]["not-now"]
    groups: dict = {}
    for row in later:
        trigger = (row.get("trigger") or "").strip()
        if trigger:
            groups.setdefault(trigger, []).append(row["identifier"])
    if not groups:
        return []
    w = ["## Not now — and when to come back", ""]
    w.append("Wanted, deliberately not this batch — and each one names what "
             "brings it back. That is 'later', and it is not 'no'.")
    w.append("")
    for trigger, ids in sorted(groups.items(),
                               key=lambda kv: (-len(kv[1]), kv[0])):
        listed = ", ".join(sorted(ids, key=_card_sort_key)[:20])
        if len(ids) > 20:
            listed += f", and {len(ids) - 20} more"
        w.append(f"- {trigger} — {_plural(len(ids), 'card')}: {listed}")
    w.append("")
    return w


def _render_cancel(proposal: dict) -> list:
    """The Cancel list, as the table DRE-4682's reader parses (DRE-4727).

    `CANCEL_HEADING`, then `CANCEL_COLUMNS` — the batch table's seven columns
    with `Reason` last — one row per card in position order, positions from 1.
    The first six cells are the batch table's own, except that the title is
    pipe-escaped: the reader splits on an unescaped pipe and takes the reason
    from the seventh cell, so a `|` in a title must not move it. The reason is
    escaped the same way and never cut, because it is the line the drain
    writes onto the card when the CEO agrees.

    Nothing at all when the list is empty — no heading, no `- None.` — since
    the reader takes an absent section to mean no cancellation proposed.
    """
    rows = sorted(proposal["outcomes"]["dead"],
                  key=lambda r: r.get("position") or 0)
    if not rows:
        return []
    w = [CANCEL_HEADING, ""]
    w.append(f"The rest of the same set: cards that no longer apply — "
             f"replaced, superseded or already done. Approving cancels each "
             f"one with its reason written on the card; `{MARK} {EXCLUDE_TAG}: "
             f"{proposal['id']} DRE-N` keeps one in {proposal['lane']}.")
    w.append("")
    w.append(CANCEL_COLUMNS)
    w.append("| -- | -- | -- | -- | -- | -- | -- |")
    for row in rows:
        w.append(f"| {row.get('position')} | {row['identifier']} | "
                 f"{BAND_LABELS.get(row.get('band'), '—')} | {row['repo']} | "
                 f"{row.get('epic') or '—'} | {_cell(row.get('title'))} | "
                 f"{_whole_cell(row.get('reason'))} |")
    w.append("")
    return w


def _render_held(proposal: dict) -> list:
    """The repos the CEO switched off, and the marker that switches one back on.

    ONE section, and only when something is held — a proposal with no hold
    renders byte for byte as it did before this card, which is what makes the
    switch cheap to leave in place. The grammar is fixed because the console
    mirrors it: `- held: <slug> · <N> cards`, and a slug with no cards in the
    lane still gets its line.
    """
    rows = proposal.get("held_repos") or []
    if not rows:
        return []
    w = ["## Held repos — switched off by you", ""]
    for row in rows:
        w.append(f"- held: {row['repo']} · {_plural(row['cards'], 'card')}")
    w.append("")
    w.append(f"Their cards are still in {proposal['lane']} and were not ranked, "
             f"not counted against the capacity and not offered here — comment "
             f"`{MARK} {REPO_RELEASE_TAG}: <slug>` on this card to switch one "
             f"back on.")
    w.append("")
    return w


def _render_unranked(proposal: dict) -> list:
    """The cards the read could not rank, as their own section.

    Never folded into "not now": a card nobody could place is not a card
    deliberately deferred, and a refusal that renders as a deferral is a
    refusal nobody ever reads. None of them is in the batch — what is owed is
    a person, not a cycle (DRE-3544).

    Nothing at all on a run carrying a `problem`, and that is the same rule:
    there the list is the WHOLE population, the read declined none of it, and
    printing every batched card under a heading that says nobody could place
    it is the contradiction this section exists to report. The line above the
    batch says what happened to that run instead.
    """
    block = proposal.get("judgement") or {}
    if not block.get("enabled") or block.get("problem"):
        return []
    if not block.get("unranked"):
        return []
    titles = {row["identifier"]: row.get("title") or ""
              for row in proposal["sequence"]}
    w = ["## Could not rank — needs a person", ""]
    w.append(f"{_plural(len(block['unranked']), 'card')} the read could not "
             f"place. None of them is in the batch, and each one wants a "
             f"human answer rather than another pass.")
    w.append("")
    for identifier in block["unranked"]:
        w.append(f"- {identifier} · {_trim(titles.get(identifier, ''))}")
    w.append("")
    return w


# --------------------------------------------------------------------------- #
# the approval gate                                                            #
# --------------------------------------------------------------------------- #

# The marker must OPEN the comment — the same anchoring the routing verdict uses,
# for the same reason: a reader that matched it anywhere would read a sentence
# ABOUT approving as an approval. Group 1 is the proposal id; group 2 is the
# rest of THAT LINE (no `$`, no re.M: `.` stops at the newline, so a marker
# followed by paragraphs of prose still reads).
_DECISION_LINES = {
    tag: re.compile(rf"^\s*(?:{MARK}\s*)?{tag}\s*:\s*([0-9a-f]{{6,}})(.*)")
    for tag in (*DECISION_TAGS, DRAINED_TAG)
}
_APPROVAL_LINE = _DECISION_LINES[APPROVAL_TAG]
# The tail of an exclusion or an addition: the card it names, then an optional
# reason after an em dash. And the tail of a decline: a reason, and nothing else.
_CARD_TAIL = re.compile(r"^\s+(DRE-\d+)\s*(?:—\s*(.*?))?\s*$")
_REASON_TAIL = re.compile(r"^\s*—\s*(\S.*?)\s*$")


def decision_match(tag: str, body: str | None):
    """`re.Match` for `tag` opening `body`, or None — the ONE matcher.

    Every marker in the vocabulary is read the same way, so a new one cannot
    quietly arrive with looser anchoring than the approval it sits beside.
    """
    pattern = _DECISION_LINES.get(tag)
    if pattern is None:                                   # pragma: no cover
        raise KeyError(f"{tag} is not one of the groomer's markers")
    return pattern.match((body or "").strip())


def _ignored(tag: str, why: str, identifier: str | None = None) -> dict:
    return {"tag": tag, "identifier": identifier, "why": why}


_BY_THE_PIPELINE = ("written by the pipeline's own Linear identity — the "
                    "proposer decides nothing about its own proposal")


def _by_the_pipeline(tag: str, record: dict) -> str:
    """Why a pipeline-written marker was dropped — and, when it carried a
    console receipt that failed, which check it failed (DRE-3754)."""
    why = f"`{tag}` {_BY_THE_PIPELINE}"
    refused = record.get("receipt_refused")
    return f"{why}; its console receipt was refused: {refused}" if refused else why


# --------------------------------------------------------------------------- #
# the console receipt (DRE-3754)                                               #
# --------------------------------------------------------------------------- #
#
# The CEO, 2026-09-12 21:28 PT: "If I sign in, I am who I am by definition."
# The console writes a signed-in owner's decision on the only Linear key it
# holds — the fleet's — so the comment's AUTHOR is the pipeline, and the gate
# above refuses it. What makes it the owner's is the console's Ed25519
# signature on its last line, over the marker, the card, the batch, the user
# and the time (`console_receipt.SPEC`). No workflow or agent holds the key
# that makes one, so the fleet still cannot approve its own proposal.
#
# `vouch` is the ONE place a receipt is checked, at the three places a thread
# is read for decisions (`_drain`, `read_holds`, `main`'s propose read). The
# readers themselves ask `_decides`, which reads the verdict `vouch` left — so
# a reader handed a thread nobody vouched for reads every fleet marker as the
# fleet's, whatever it carries. Fail closed at the seam.

#: The verdict `vouch` leaves on a record whose receipt verified.
VOUCHED_BY_CONSOLE = "console receipt"

#: One verifier per run: the console's key is fetched at most once, lazily, on
#: the first receipt that needs it — never for a marker with no receipt.
_VERIFIER: console_receipt.Verifier | None = None


def _verifier() -> console_receipt.Verifier:
    global _VERIFIER
    if _VERIFIER is None:
        _VERIFIER = console_receipt.Verifier()
    return _VERIFIER


def _decides(record: dict) -> bool:
    """Is this comment a decision somebody other than the proposer made?

    Yes when a person wrote it (the author is not the pipeline's own Linear
    identity, exactly as before), or when the pipeline's key wrote it and
    `vouch` verified the console's receipt on it."""
    return (not record.get("authored_by_pipeline")
            or record.get("vouched_by") == VOUCHED_BY_CONSOLE)


def _marker_subject(body: str) -> str | None:
    """The batch a decision marker opening `body` names — `console_receipt.
    NO_PROPOSAL` for a repo switch, which names none — or None when the
    comment is not one of the CEO's markers at all."""
    for tag in DECISION_TAGS:
        found = decision_match(tag, body)
        if found:
            return found.group(1)
    for tag in REPO_TAGS:
        if repo_switch_match(tag, body):
            return console_receipt.NO_PROPOSAL
    return None


def vouch(records: list[dict], *, card: str,
          verifier: console_receipt.Verifier | None = None) -> list[dict]:
    """The thread, with every pipeline-written marker's console receipt checked.

    Returns copies. A record the pipeline did not write is untouched — the
    CEO's own marker decides exactly as it always has, and never makes this
    fetch a key. A pipeline-written marker with NO receipt is untouched too,
    and so refused exactly as today. One WITH a receipt gets either
    `vouched_by` (it decides) or `receipt_refused` (why it does not).

    A receipt decides ONCE, at the first comment in the thread that carries
    it. The key is the SIGNED CONTENT, not the signature's spelling — base64
    has more than one spelling of the same bytes — so the fleet re-posting the
    CEO's approval after the CEO declined cannot make the copy the newest word.
    """
    honoured: set[str] = set()
    out = []
    for record in records:
        row = dict(record)
        out.append(row)
        if not row.get("authored_by_pipeline"):
            continue
        body = row.get("body") or ""
        proposal = _marker_subject(body)
        if proposal is None or not console_receipt.has_trailer(body):
            continue
        receipt = console_receipt.parse(body)
        signed = (hashlib.sha256(console_receipt.signed_bytes(
            console_receipt.marker_line(body), card, proposal, receipt.user,
            receipt.at)).hexdigest() if receipt else None)
        if signed and signed in honoured:
            row["receipt_refused"] = (
                "its console receipt was already honoured on an earlier "
                "comment in this thread — a receipt decides once")
            continue
        why = (verifier or _verifier()).check(
            body, card=card, proposal=proposal,
            created_at=row.get("created_at"))
        if why:
            row["receipt_refused"] = why
            print(f"groomer: `{console_receipt.marker_line(body)}` on {card} "
                  f"was not honoured — {why}", file=sys.stderr)
            continue
        honoured.add(signed)
        row["vouched_by"] = VOUCHED_BY_CONSOLE
        print(f"groomer: `{console_receipt.marker_line(body)}` on {card} is "
              f"honoured on a console receipt — console user {receipt.user}, "
              f"signed {receipt.at}", file=sys.stderr)
    return out


def decision_records(lops, card: str, *, whole_thread: bool = False) -> list[dict]:
    """`lops.comment_records(card)`, vouched — the ONE read of a thread the
    CEO's decisions are taken from. The window read is asked for exactly as
    it always was, so a caller's reader sees the same call it did before."""
    raw = (lops.comment_records(card, whole_thread=True) if whole_thread
           else lops.comment_records(card))
    return vouch(raw, card=card)


def read_decisions(records: list[dict]) -> dict:
    """Every decision the CEO wrote on this card, read in one pass.

    Returns `{"approved", "problem", "excluded", "added", "ignored",
    "drained"}`. `approved` is the id of the batch that is currently approved
    and `problem` says why there isn't one; `excluded` and `added` map a card
    identifier to the marker that named it LAST, because a card named by both
    follows the newest comment; `ignored` is every marker the drain would not
    honour, each with the reason, so the record can say what it dropped;
    `drained` is every batch a `groom-drained` record already stands for.

    The NEWEST human decision wins, for the approval as it always has and for
    the decline beside it — a thread carries several as batches are re-proposed
    and re-argued, and the last word is the one that is current.
    """
    # (thread index, id[, reason]) — the index is how "the newest decision"
    # is decided between the two lists without either of them being scanned
    # against the other.
    approvals: list[tuple[int, str]] = []
    declines: list[tuple[int, str, str]] = []
    by_pipeline_approval = False
    # Why the pipeline's own approval did not count when it carried a console
    # receipt that failed (DRE-3754) — named in the refusal, so a click that
    # did not count says which check it failed rather than only who wrote it.
    approval_receipt_refused: str | None = None
    per_card: list[tuple[str, str, str, str | None]] = []   # tag, pid, card, why
    ignored: list[dict] = []
    drained: list[str] = []

    for index, record in enumerate(records):
        body = record.get("body") or ""
        mine = not _decides(record)

        drain = decision_match(DRAINED_TAG, body)
        if drain:
            drained.append(drain.group(1))
            continue

        for tag in DECISION_TAGS:
            match = decision_match(tag, body)
            if not match:
                continue
            pid, tail = match.group(1), match.group(2)
            if mine:
                # The gate, applied to the whole vocabulary and not only to the
                # approval: the proposer cannot approve, decline, exclude or
                # add anything — unless the console signed it (DRE-3754), in
                # which case `_decides` already said so.
                if tag == APPROVAL_TAG:
                    by_pipeline_approval = True
                    approval_receipt_refused = (record.get("receipt_refused")
                                                or approval_receipt_refused)
                else:
                    ignored.append(_ignored(
                        tag, _by_the_pipeline(tag, record),
                        _named_card(tail)))
                break
            if tag == APPROVAL_TAG:
                approvals.append((index, pid))
            elif tag == DECLINE_TAG:
                reason = _REASON_TAIL.match(tail)
                if not reason:
                    # A decline nobody can act on. Read as absent rather than
                    # as a refusal: "no" with no reason leaves the CEO having
                    # stopped a batch and nobody able to fix it.
                    ignored.append(_ignored(
                        tag, f"`{tag}` carries no reason after the `—`, and a "
                             f"decline without one names nothing anybody can "
                             f"fix — it was read as absent"))
                else:
                    declines.append((index, pid, reason.group(1)))
            else:
                named = _CARD_TAIL.match(tail)
                if not named:
                    ignored.append(_ignored(
                        tag, f"`{tag}` names no card — the shape is "
                             f"`{MARK} {tag}: <id> DRE-N[ — <reason>]`"))
                else:
                    per_card.append((tag, pid, named.group(1),
                                     (named.group(2) or "").strip() or None))
            break

    approved, problem = _current_decision(approvals, declines,
                                          by_pipeline_approval,
                                          approval_receipt_refused)
    excluded, added = {}, {}
    for tag, pid, identifier, reason in per_card:
        if approved and pid != approved:
            ignored.append(_ignored(
                tag, f"`{tag}` names batch `{pid}`, and the batch approved on "
                     f"this card is `{approved}`", identifier))
            continue
        # The newest comment wins: a later marker replaces an earlier one for
        # the same card, whichever of the two markers each of them is.
        excluded.pop(identifier, None)
        added.pop(identifier, None)
        (excluded if tag == EXCLUDE_TAG else added)[identifier] = {
            "tag": tag, "reason": reason}
    return {"approved": approved, "problem": problem, "excluded": excluded,
            "added": added, "ignored": ignored, "drained": drained}


def _named_card(tail: str) -> str | None:
    """The card an exclusion or addition names, for a marker being reported
    rather than honoured. None on a decline, which names none."""
    found = _CARD_TAIL.match(tail)
    return found.group(1) if found else None


def _current_decision(approvals: list[tuple[int, str]],
                      declines: list[tuple[int, str, str]],
                      by_pipeline_approval: bool,
                      receipt_refused: str | None = None,
                      ) -> tuple[str | None, str | None]:
    """The last word on this card: `(approved id, None)` or `(None, why not)`.

    A CEO who approves a batch and then declines it has declined it, and one
    who declines and then approves has approved it. Both lists carry the thread
    index each decision was written at, so "the newest" is a comparison and
    never an assumption about which marker outranks which.
    """
    if approvals or declines:
        newest_approval = approvals[-1][0] if approvals else -1
        if declines and declines[-1][0] > newest_approval:
            _, pid, reason = declines[-1]
            return None, (f"the newest decision on this card is a decline of "
                          f"batch `{pid}`: {reason}")
        return approvals[-1][1], None
    if by_pipeline_approval:
        refused = (f"; its console receipt was refused: {receipt_refused}"
                   if receipt_refused else "")
        return None, ("the only approval on this card was written by the "
                      "pipeline's own Linear identity — the proposer cannot "
                      f"approve its own proposal{refused}")
    return None, (f"no comment on this card opens with "
                  f"`{MARK} {APPROVAL_TAG}: <proposal id>` — nothing leaves "
                  f"Intake without the CEO approving a batch")


def approved_id(records: list[dict]) -> tuple[str | None, str | None]:
    """The proposal id the CEO approved on this card, or why there isn't one.

    Kept as the narrow reading of `read_decisions` for callers that only ask
    the yes/no question.
    """
    decisions = read_decisions(records)
    return decisions["approved"], decisions["problem"]


# --------------------------------------------------------------------------- #
# the repo switch (DRE-3403)                                                   #
# --------------------------------------------------------------------------- #

# The same anchoring `decision_match` uses, and for the same reason: a reader
# that matched the marker anywhere would read a sentence ABOUT holding a repo
# as a hold. What differs is the tail — a SLUG, `[a-z0-9][a-z0-9-]*`, and no
# proposal id, because a hold outlives the proposal it was written on. The
# lookahead is what stops `atlas.beta` reading as `atlas`: a slug that does not
# end where the label's own slug ends is not this repo's slug, and holding the
# wrong repo is worse than holding none.
_REPO_SWITCH_LINES = {
    tag: re.compile(rf"^\s*(?:{MARK}\s*)?{tag}\s*:\s*([a-z0-9][a-z0-9-]*)(?=\s|$)")
    for tag in REPO_TAGS
}


def repo_switch_match(tag: str, body: str | None):
    """`re.Match` for a repo switch opening `body`, or None — the ONE matcher.

    Group 1 is the repo slug. Both switches are read here, so a hold and a
    release cannot quietly arrive with different anchoring.
    """
    pattern = _REPO_SWITCH_LINES.get(tag)
    if pattern is None:                                   # pragma: no cover
        raise KeyError(f"{tag} is not one of the groomer's repo switches")
    return pattern.match((body or "").strip())


def held_repos(records: list[dict]) -> list[str]:
    """The repos that are switched off right now, read off a whole thread.

    The NEWEST marker per slug wins — a release after a hold switches the repo
    back on, and a hold after that switches it off again — so this is one
    switch read in thread order rather than two lists that can disagree.
    `records` is `linear_ops.comment_records(card, whole_thread=True)`: a hold
    is not bound to a proposal, so the current answer can be the OLDEST comment
    on the card, and the fifty-comment window would lose it.

    Honoured only when the author is not the pipeline's own Linear identity
    (DRE-2721). The gate matters most in the release direction: a proposer that
    could switch a repo back on could undo the CEO's own answer and propose the
    cards anyway. A marker it wrote is ignored and named in the run log, so a
    hold that did nothing is visible rather than silent.
    """
    switch: dict[str, bool] = {}
    ignored: list[tuple[str, str]] = []
    for record in records:
        body = record.get("body") or ""
        for tag in REPO_TAGS:
            match = repo_switch_match(tag, body)
            if not match:
                continue
            if not _decides(record):
                ignored.append((tag, match.group(1),
                                record.get("receipt_refused")))
            else:
                switch[match.group(1)] = tag == REPO_HOLD_TAG
            break
    for tag, slug, refused in ignored:
        receipt = (f" (its console receipt was refused: {refused})"
                   if refused else "")
        print(f"groomer: `{MARK} {tag}: {slug}` was written by the pipeline's "
              f"own Linear identity{receipt}, so it was ignored — the proposer "
              f"does not switch a repo off or back on", file=sys.stderr)
    return sorted(slug for slug, held in switch.items() if held)


def offered(cards: list[dict], held) -> list[dict]:
    """The lane minus every card whose repo is switched off (DRE-3403).

    ONE definition. `_build` filters before it builds the census the model
    reads and `propose` filters before it sequences, and two filters is two
    chances for a held card to reach the model or the batch.
    """
    slugs = set(held)
    return [card for card in cards if repo_of(card) not in slugs]


def held_repo_rows(cards: list[dict], held) -> list[dict]:
    """`[{"repo": <slug>, "cards": <n>}]` — one row per held slug, biggest
    first, then alphabetical.

    A slug with no cards in the lane still gets a row: the CEO switched that
    repo off, and a hold that rendered nothing would read as a hold nobody
    honoured.
    """
    counts = Counter(repo_of(card) for card in cards)
    return sorted(({"repo": slug, "cards": counts.get(slug, 0)}
                   for slug in dict.fromkeys(held)),
                  key=lambda row: (-row["cards"], row["repo"]))


def blocked_by_held(on_offer: list[dict], cards: list[dict], held) -> list[dict]:
    """Batchable cards a card in a HELD repo blocks, one row per pair.

    The hold takes the blocker out of the offer, and the ordering constraint
    goes with it — so the blocked card sequences as though nothing held it.
    It is NAMED rather than dropped: `blockedBy` is the relation the promotion
    gate reads, so the dependency gate holds it later anyway, and a groomer
    that quietly removed it would be answering a question nobody asked it.
    """
    slugs = set(held)
    held_cards = {card["identifier"]: repo_of(card) for card in cards
                  if repo_of(card) in slugs}
    rows = []
    for card in on_offer:
        for blocker in blockers_of(card):
            if blocker in held_cards:
                rows.append({"identifier": card["identifier"],
                             "blocked_by": blocker,
                             "repo": held_cards[blocker]})
    return sorted(rows, key=lambda row: (_card_sort_key(row["identifier"]),
                                         _card_sort_key(row["blocked_by"])))


# The proposal's own heading, and the one line that names the lane. Both are
# written by `render_proposal` a few dozen lines up, and read back here: the
# render and this parser are two halves of ONE contract, so the round trip is
# asserted in tests/test_groomer_approval_gate.py rather than assumed.
#
# `[ \t]`, NEVER `\s` (DRE-3712). `\s` matches a newline, so `cycle\s+(.*)`
# walked off the end of a heading with no cycle on it, across the blank line,
# and captured the paragraph below — which is how reading the 2026-09-04
# comment on DRE-2840 answers "cycles 0 and 0", two numbers quoted out of a
# sentence. The clause is optional here because `title_line` stops writing it
# when there is no cycle to name, and old comments do not re-render: the reader
# meets both shapes and must take a cycle from neither wrongly. This is the
# same fix as the console's copy of the pattern, which is where the CEO saw it
# (`console/backend/groom_proposal.py` in agent-bureau, DRE-3708).
_PROPOSAL_HEADING = re.compile(
    r"^#[ \t]+Groom proposal[ \t]+`([0-9a-f]{6,})`"
    r"(?:[ \t]+—[ \t]+cycle[ \t]*(.*))?$", re.M)
_LANE_LINE = re.compile(
    r"^\d+[ \t]+cards?[ \t]+of[ \t]+\d+[ \t]+in[ \t]+(.+?)[ \t]+are proposed"
    r"(?:[ \t]+for cycle\b|,)", re.M)
_BATCH_HEADING = "## The batch, in order"
_BATCH_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(DRE-\d+)\s*\|")
# A cell boundary in the Cancel table: a pipe no backslash escapes. The render
# escapes every pipe inside a title or a reason (`_whole_cell`), so splitting
# here puts the reason in the seventh cell whatever the title says (DRE-4727).
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def parse_proposal_comment(body: str | None) -> dict | None:
    """A posted proposal comment, read back as the record the drain moves.

    `{"id", "lane", "cycles", "batch": [{"identifier", "position", …}]}`, or
    None when the comment is not a proposal. The marker must OPEN the comment,
    the same anchoring `already_proposed` uses and for the same reason: a
    comment that mentions the marker mid-sentence is prose ABOUT a proposal.

    Only the first two cells of a batch row are load-bearing — the position and
    the card. The rest are read defensively, because a card title carrying a
    pipe would shift every column after it and the drain must not move a
    different card because somebody wrote a `|` in a title.

    Since DRE-4733 the record also carries `"cancel": [{"identifier",
    "position", "repo", "reason"}]`, read from the table under
    `CANCEL_HEADING` (DRE-4727): cells split on an unescaped pipe, the reason
    the SEVENTH cell with `\\|` unescaped, because the reason is the line the
    drain writes onto the card it cancels. No section reads as `[]` — the
    render leaves it out when nothing is proposed for cancellation.
    """
    text = body or ""
    marker = _PROPOSAL_LINE.match(text.strip())
    if not marker:
        return None
    heading = _PROPOSAL_HEADING.search(text)
    lane = _LANE_LINE.search(text)
    batch = []
    for line in _section(text, _BATCH_HEADING):
        row = _BATCH_ROW.match(line.strip())
        if not row:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        batch.append({
            "position": int(row.group(1)),
            "identifier": row.group(2),
            "repo": cells[3] if len(cells) > 3 else "",
            "epic": cells[4] if len(cells) > 4 else "",
            "title": cells[5] if len(cells) > 5 else "",
        })
    cancel = []
    for line in _section(text, CANCEL_HEADING):
        row = _BATCH_ROW.match(line.strip())
        if not row:
            continue
        # The first and last pieces are the outside of the leading and
        # trailing pipes, so the seven cells are everything between.
        cells = [c.strip() for c in _UNESCAPED_PIPE.split(line.strip())[1:-1]]
        cancel.append({
            "identifier": row.group(2),
            "position": int(row.group(1)),
            "repo": cells[3] if len(cells) > 3 else "",
            "reason": cells[6].replace("\\|", "|") if len(cells) > 6 else "",
        })
    return {
        "id": marker.group(1),
        "lane": lane.group(1).strip() if lane else None,
        # `group(2)` is None on a heading that names no cycle — a batch with no
        # cards, and the drain has nothing to assign anyway (DRE-3712).
        "cycles": [int(n) for n in re.findall(r"\d+", heading.group(2) or "")]
                  if heading else [],
        "batch": sorted(batch, key=lambda r: r["position"]),
        "cancel": sorted(cancel, key=lambda r: r["position"]),
    }


def _section(text: str, heading: str) -> list:
    """The lines under `heading`, up to the next `## ` heading — none when the
    heading is absent."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.strip() == heading:
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside:
            lines.append(line)
    return lines


def proposal_record(pid: str, records: list[dict]) -> dict | None:
    """The proposal comment on this card carrying `pid`, parsed — or None.

    The comment and not the run artifact, deliberately. Three reasons, and the
    first is the whole point: the comment is the thing the CEO actually READ
    before approving, and the approval is a reply to it in the same thread, so
    the record and the consent to it are one object read with one credential.
    The artifact is a by-product of a run — it expires (30 days), it needs a
    second credential into GitHub Actions, and finding the right one means
    searching runs for the id, which is a re-derivation of a different kind.
    """
    for record in records:
        parsed = parse_proposal_comment(record.get("body"))
        if parsed and parsed["id"] == pid:
            return parsed
    return None


def drain(lops, *, card: str, lane: str = "Intake", to: str = DRAIN_TO) -> dict:
    """Execute exactly what the CEO agreed: the approved Planning list to
    Planning and the approved Cancel list to Canceled — minus every exclusion
    on either list and every card in a repo the CEO has switched off, plus
    every addition — in the order the record carries.

    The batch is READ, never re-derived (DRE-3338): the approval names an id,
    the proposal comment carrying that id is the record, and its two tables
    are the two lists (DRE-4727). The CEO's per-card decisions (DRE-3370) are
    read off the same thread and adjust them: an excluded card stays where it
    is, whichever list it is on; an added card moves to Planning after the
    batch, on the batch's own cycle. The repo switch (DRE-3403) is read off
    that same thread at DRAIN time, so a hold written after the proposal was
    posted still holds its cards back — `held back`, `repo held: <slug>` —
    while the rest moves. No model is called and the population is never
    re-read — a drain is a move, not a judgement.

    An agreed Cancel card is cancelled with its reason written on it first,
    as `🧺 groom-cancelled: <id> — <reason>`, and moves to `Canceled` — never
    `Done`, and never onto a cycle (DRE-4733). A card on either list that has
    already left the lane since the proposal is an `already gone` row naming
    the lane it is in now, and the rest of the agreement still stands.

    Refuses, before any card moves: a closed pen, a closing destination, a
    missing / pipeline-written / declined approval, an approval whose proposal
    is not on the card, a batch already drained, and a cycle Linear does not
    carry. Every refusal but the destination is written onto the card as
    `🧺 groom-drain-refused: <id> — <reason>`.
    """
    if to in NEVER_WRITES or to == CANCEL_TO:
        # Before the card is read, so there is no batch to name and nothing to
        # write down: a bad invocation, not a refused batch.
        raise WillNotClose(
            f"the drain will not write the Planning list to {to!r}: it never "
            f"closes a card, and it writes {CANCEL_TO!r} only for an agreed row "
            f"of the approved Cancel table")
    try:
        return _drain(lops, card=card, lane=lane, to=to)
    except DrainRefused as refusal:
        # ONE writer for every refusal, at the ONE place they all pass through.
        # A drain that refuses a batch and says so only in a workflow log is a
        # stall with an alibi, and a refusal each raiser had to remember to
        # post is a refusal one of them eventually will not.
        lops.cmd_comment(card, drain_refused_record(refusal.batch, str(refusal)))
        raise


def _drain(lops, *, card: str, lane: str = "Intake", to: str = DRAIN_TO) -> dict:
    """`drain` proper — every exit is a return or a DrainRefused.

    The defaults are `drain`'s own, restated rather than dropped: the lane a
    write reaches is read off the call site's enclosing signature
    (`ready_lane_writers.py`), and a parameter with no default is a destination
    nothing can read. The Cancel list's destination is not a parameter at all:
    it is `CANCEL_TO`, named at its own call site.
    """
    # Reads only, and they decide nothing yet: the hold below has to be able to
    # say how big the batch behind the pen is, and the only place that number
    # exists now is the record itself. The WHOLE thread, paginated: a proposal
    # card carries one comment per per-card decision, and the approval is the
    # OLDEST of them — the first thing to fall out of a fifty-comment window.
    records = decision_records(lops, card, whole_thread=True)
    decisions = read_decisions(records)
    pid, problem = decisions["approved"], decisions["problem"]
    record = proposal_record(pid, records) if pid else None
    # The id a refusal names: the approved batch, else the newest proposal on
    # the card, else nothing readable at all.
    named = pid or _newest_proposal_id(records) or NO_BATCH_ID

    if INTAKE_HOLD is not None:
        # Ahead of the approval's verdict: the hold is the operator's answer
        # about the LANE, not about this batch. Said out loud once per pass so
        # a held drain reads as refused rather than as a run that moved nothing.
        raise IntakeHeld(intake_controls.notice(
            INTAKE_HOLD,
            len(record["batch"]) if record else 0,
            f"the batch approved on {card}" if record
            else f"no approved batch on {card}"), batch=named)
    if problem:
        raise NotApproved(problem, batch=named)
    if pid in decisions["drained"]:
        raise AlreadyDrained(
            f"batch {pid} has already been drained — a `{MARK} {DRAINED_TAG}: "
            f"{pid}` record stands on {card}, and that record is the receipt "
            f"for cards that have already moved; re-propose if the lane needs "
            f"draining again", batch=named)
    if record is None:
        raise NoProposalRecord(
            f"the approval on {card} names batch {pid}, and no proposal comment "
            f"on this card carries that id — the drain moves the batch that was "
            f"written down, so re-post the proposal (it may have scrolled out of "
            f"the comments this reads) and approve it again", batch=named)

    lane = record["lane"] or lane
    cycle = _approved_cycle(lops, record)
    # The repo switch, read at DRAIN time off the same thread (DRE-3403), so a
    # hold written AFTER the proposal was posted never drains a card the CEO
    # has switched off.
    held = held_repos(records)
    plan = _drain_plan(record, decisions, lane=lane, held=held)

    # Every card the drain would write to, read ONCE and before anything
    # moves. A card that has left the lane since the proposal — Done, already
    # Canceled, moved by hand — cannot be moved, and the rest of what the CEO
    # agreed still stands (DRE-4733): it is an `already gone` row naming the
    # lane it is in now. It used to refuse the whole batch, which on 2026-09-16
    # cost the CEO a second approval of a batch two stale rows away from right.
    issues, gone = {}, {}
    for row in plan["moving"] + plan["cancelling"]:
        issues[row["identifier"]] = issue = lops.get_issue(row["identifier"])
        now = ((issue or {}).get("state") or {}).get("name")
        if now != lane:
            gone[row["identifier"]] = now or "a lane this run could not read"
    if gone:
        # The same pure plan with those cards taken out of the writes, so each
        # one's row says what happened to it and nothing is written to it.
        plan = _drain_plan(record, decisions, lane=lane, held=held, gone=gone)

    moved = []
    for row in plan["moving"]:
        if cycle[1]:
            # Only a cycle the record actually named. A proposal with an empty
            # batch resolves none, and writing `cycleId: null` onto a card an
            # addition reached in for would CLEAR the cycle it already had —
            # a silent edit nobody approved.
            lops.gql(SET_CYCLE, {"id": issues[row["identifier"]]["id"],
                                 "input": {"cycleId": cycle[1]}})
        lops.cmd_state(row["identifier"], to)
        moved.append(row["identifier"])

    cancelled = []
    for row in plan["cancelling"]:
        # The reason first, then the lane, and no cycle: a cancelled card is
        # not scheduled. A crash between the two leaves a card in Intake
        # carrying a note that names the batch — visible, and re-proposable —
        # never a Canceled card nobody can say why about.
        lops.cmd_comment(row["identifier"],
                         cancelled_note(record["id"], row["reason"]))
        lops.cmd_state(row["identifier"], CANCEL_TO)
        cancelled.append(row["identifier"])

    result = {"moved": moved,
              "held_back": [r["identifier"] for r in plan["held_back"]],
              "added": [r["identifier"] for r in plan["moving"]
                        if r["outcome"] == "added"],
              "cancelled": cancelled,
              "already_gone": list(gone),
              "refused": plan["ignored"],
              "rows": plan["rows"], "to": to, "from": lane,
              "cycle": cycle[0], "proposal": record["id"]}
    lops.cmd_comment(card, drained_record(result))
    return result


def _drain_plan(record: dict, decisions: dict, *, lane: str, held=(),
                gone: dict | None = None) -> dict:
    """Both lists minus the exclusions and the held repos, plus the additions,
    and the table row every card involved gets.

    Rows follow the PROPOSAL's own order first, so a reader can lay the record
    beside the proposal and go down both together: the Planning list, then the
    additions in the order the CEO wrote them, then the Cancel list, then any
    decision the drain would not honour. One row per card on either list.

    An exclusion holds a card back on EITHER list, and so does a repo the CEO
    has switched off — through the SAME per-card row (DRE-3403): one way for a
    card to stay behind, one row shape in the record. An addition only ever
    reaches the Planning list: one naming a card on the Cancel list is the
    CEO's word that it is wanted, so it moves to Planning and is not cancelled.

    `gone` maps a card to the lane it is in now when it has left `lane` since
    the proposal (DRE-4733): it is taken out of every write and its row says
    `already gone`, with that lane as the why.
    """
    excluded, added = decisions["excluded"], dict(decisions["added"])
    gone = gone or {}
    switched_off = set(held)
    in_batch = {row["identifier"] for row in record["batch"]}
    to_cancel = record.get("cancel") or []
    on_a_list = in_batch | {row["identifier"] for row in to_cancel}
    ignored = list(decisions["ignored"])

    # An exclusion can only take a card OFF a list. One naming a card on
    # neither holds nothing back — and reading it as a decision would let a
    # marker invent a card into a record it is not in.
    for identifier, mark in excluded.items():
        if identifier not in on_a_list:
            ignored.append(_ignored(
                mark["tag"],
                f"`{EXCLUDE_TAG}` names a card on neither approved list, so "
                f"there was nothing to hold back", identifier))

    moving, cancelling, held_back, rows = [], [], [], []

    def kept(row) -> bool:
        """Held back by an exclusion or a switched-off repo — its row written."""
        identifier = row["identifier"]
        mark = excluded.get(identifier)
        if mark:
            held_back.append(row)
            rows.append({"identifier": identifier, "outcome": "held back",
                         "why": _marker_why(mark)})
            return True
        # The record's own repo cell — the repo the CEO was looking at when
        # they approved the batch, and the one the hold is about.
        if row.get("repo") in switched_off:
            held_back.append(row)
            rows.append({"identifier": identifier, "outcome": "held back",
                         "why": f"repo held: {row['repo']}"})
            return True
        return False

    def left(identifier: str) -> bool:
        """Already out of the lane — its row written, and no write owed."""
        if identifier not in gone:
            return False
        rows.append({"identifier": identifier, "outcome": "already gone",
                     "why": gone[identifier]})
        return True

    for row in record["batch"]:
        identifier = row["identifier"]
        if kept(row):
            continue
        if identifier in added:
            # Already in the batch: the addition asks for something that is
            # happening anyway, so it is reported rather than acted on.
            ignored.append(_ignored(
                added.pop(identifier)["tag"],
                f"`{ADD_TAG}` names a card already in the approved batch",
                identifier))
        if left(identifier):
            continue
        moving.append({**row, "outcome": "moved"})
        rows.append({"identifier": identifier, "outcome": "moved",
                     "why": f"proposal `{record['id']}` position "
                            f"{row['position']}"})

    for identifier, mark in added.items():
        if left(identifier):
            continue
        moving.append({"identifier": identifier, "position": None,
                       "outcome": "added"})
        rows.append({"identifier": identifier, "outcome": "added",
                     "why": _marker_why(mark)})

    for row in to_cancel:
        identifier = row["identifier"]
        if identifier in added:
            # Its row is the addition's, written above: it went to Planning.
            continue
        if kept(row) or left(identifier):
            continue
        cancelling.append({**row, "outcome": "cancelled"})
        rows.append({"identifier": identifier, "outcome": "cancelled",
                     "why": f"proposal `{record['id']}` Cancel position "
                            f"{row['position']} — {_whole_cell(row['reason'])}"})

    for mark in ignored:
        if mark["identifier"] and mark["identifier"] in on_a_list:
            # The card has a row already, carrying what actually happened to
            # it. The dropped marker is named there rather than contradicted by
            # a second row for the same card.
            row = next(r for r in rows if r["identifier"] == mark["identifier"])
            row["why"] += f" — {mark['why']}, so it was ignored"
            continue
        rows.append({"identifier": mark["identifier"] or "—",
                     "outcome": "refused", "why": mark["why"]})
    return {"moving": moving, "cancelling": cancelling, "held_back": held_back,
            "rows": rows, "ignored": ignored, "lane": lane}


def _marker_why(mark: dict) -> str:
    """The Why cell for a card an exclusion or addition named: the marker, and
    the CEO's reason when they wrote one."""
    why = f"`{MARK} {mark['tag']}`"
    return f"{why} — {mark['reason']}" if mark.get("reason") else why


def _newest_proposal_id(records: list[dict]) -> str | None:
    """The id of the newest proposal comment on the card, for a refusal that
    has no approval to name a batch with."""
    found = None
    for entry in records:
        parsed = parse_proposal_comment(entry.get("body"))
        if parsed:
            found = parsed["id"]
    return found


def _approved_cycle(lops, record: dict) -> tuple[int | None, str | None]:
    """The cycle the approved batch names, and Linear's id for it.

    The record carries the NUMBER, which is what a person reads; the id is a
    uuid that appears nowhere on the page, so it is resolved live. That is the
    one live read a drain makes about the batch, and it can only ever change
    which container the approved cards land in — never which cards they are.
    """
    if not record["batch"]:
        return None, None
    numbers = record["cycles"]
    if not numbers:
        raise CycleRefused(
            f"proposal {record['id']} names no cycle, so the drain has nothing "
            f"to assign its batch to", batch=record["id"])
    if len(numbers) > 1:
        raise CycleRefused(
            f"proposal {record['id']} spans cycles "
            f"{', '.join(str(n) for n in numbers)} and its batch table does not "
            f"say which card belongs to which — re-propose with --batch-cycles 1 "
            f"so the record answers the question the drain has to ask",
            batch=record["id"])
    known = cycle_ids(lops)
    if numbers[0] not in known:
        raise CycleRefused(
            f"the approved batch names cycle {numbers[0]} and Linear carries no "
            f"open cycle with that number — create it first; the groomer will "
            f"not invent one", batch=record["id"])
    return numbers[0], known[numbers[0]]


def drained_record(result: dict) -> str:
    """What the drain did, per card, on the proposal card (DRE-3326, DRE-3370,
    DRE-4733).

    ONE record per drain, in a fixed grammar the console mirrors: the marker,
    then the summary line, then a row per card on either list. Undoing a bad
    batch means knowing which cards THIS drain took and which it did not, and a
    run log is not on the card.

    The summary line is written for its reader, the console's (DRE-4682 in
    agent-bureau): `moved: N · held back: N · added: N · cancelled: N ·
    refused: N → Planning at <time PT>`. That is the line as it stood before
    DRE-4733 with ONE clause, `cancelled:`, placed before `refused:` — the
    reader takes it as optional and reads the other four exactly as they were,
    so no clause is ever reordered, respelled or added beside it.

    The counts answer five different questions and none of them substitutes
    for another: `moved` is the approved Planning list that went, `held back`
    is what the CEO excluded on either list and is still in the lane, `added`
    is what the CEO reached in for, `cancelled` is the agreed Cancel list that
    went to Canceled, and `refused` counts the DECISIONS the drain would not
    honour — a marker the pipeline wrote, a decline with no reason, an
    exclusion naming a card on neither list. That last count is of decisions
    and not of rows, because a refused decision about a card that moved anyway
    is named on that card's own row: no card gets two. A card that had
    already left the lane is an `already gone` row and is counted on no clause.
    """
    pid = result["proposal"]
    rows = result["rows"]
    counts = Counter(row["outcome"] for row in rows)
    w = [f"{MARK} {DRAINED_TAG}: {pid}", "",
         f"moved: {counts['moved']} · held back: {counts['held back']} · "
         f"added: {counts['added']} · cancelled: {counts['cancelled']} · "
         f"refused: {len(result['refused'])} → "
         f"{result['to']} at {dead_run.pacific(datetime.now(timezone.utc))}",
         ""]
    if result["cycle"]:
        w += [f"Out of {result['from']}, into cycle {result['cycle']}, in the "
              f"order proposal `{pid}` was approved in.", ""]
    w += ["| # | Card | Outcome | Why |", "| -- | -- | -- | -- |"]
    order = {identifier: n for n, identifier in enumerate(result["moved"], 1)}
    for row in rows:
        w.append(f"| {order.get(row['identifier'], '—')} | "
                 f"{row['identifier']} | {row['outcome']} | {row['why']} |")
    return "\n".join(w).rstrip() + "\n"


def cancelled_note(pid: str, reason: str) -> str:
    """`🧺 groom-cancelled: <proposal id> — <reason>`, on the card the drain
    cancels, and nothing else (DRE-4733).

    One line, whitespace collapsed, the reason whole: it is the Cancel row the
    CEO agreed to, so the card says why it was closed and which batch closed
    it. Written BEFORE the card's lane changes, so a card is never Canceled
    without it.
    """
    return (f"{MARK} {CANCELLED_TAG}: {pid} — "
            f"{' '.join((reason or 'no reason given').split())}\n")


def drain_refused_record(pid: str, reason: str) -> str:
    """`🧺 groom-drain-refused: <id> — <reason>`, and nothing else.

    One line, because the console reads its grammar and a refusal that ran to
    three paragraphs would be a shape nobody could parse. Whitespace in the
    reason is collapsed for the same reason — `intake_controls.notice` writes
    over several lines and all of it belongs on this one.
    """
    return (f"{MARK} {DRAIN_REFUSED_TAG}: {pid} — "
            f"{' '.join((reason or 'no reason given').split())}\n")


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _created(card: dict) -> str:
    """The CREATION date, and never the last update (DRE-3096). A stray agent
    comment bumps `updatedAt` and must not bump a card up the batch; the
    population query does not even ask for it."""
    return card.get("createdAt") or ""


def _moment(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _epoch(iso: str) -> float:
    moment = _moment(iso)
    return moment.timestamp() if moment else 0.0


def _day_ordinal(iso: str) -> int:
    """The UTC day a card was created on — the granularity the repo tie-break
    is defined at, so two cards created eleven hours apart on one day are a tie
    that Portico wins."""
    moment = _moment(iso)
    return moment.date().toordinal() if moment else 0


def _card_sort_key(identifier: str):
    """`DRE-9` before `DRE-11`: a lexical sort of identifiers is deterministic
    but reads as arbitrary in a proposal a human has to follow."""
    match = re.search(r"(\d+)$", identifier or "")
    return (int(match.group(1)) if match else 0, identifier or "")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _trim(text: str, width: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 1] + "…"


def _line(text: str | None) -> str:
    """One line of prose, whole. `_cell`'s opposite number: no width, and no
    pipe to escape, because this one is not in a table (DRE-3764)."""
    return " ".join((text or "—").split())


def _cell(text: str | None, width: int = 60) -> str:
    """One markdown table cell. A pipe inside it would end the column early and
    silently shift every cell after it, and this text is written by a model."""
    return _trim((text or "—").replace("|", "\\|"), width)


def _whole_cell(text: str | None) -> str:
    """`_cell` with no width: one line, pipes escaped, nothing cut. The Cancel
    table's reason is the line written onto the card, so it is never
    truncated (DRE-4727)."""
    return _line(text).replace("|", "\\|")


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _shaping(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lane", default="Intake")
    parser.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY)
    parser.add_argument("--batch-cycles", type=int, default=1)
    parser.add_argument("--priority", default=",".join(REPO_PRIORITY),
                        help="comma-separated repo slugs, highest first — a "
                             "tie-break inside a day, not the master key")
    parser.add_argument("--window-days", type=int, default=WINDOW_DAYS,
                        help="accepted and changes nothing since DRE-4725: "
                             "the batch is oldest first and no card is left "
                             "out for its age (default %(default)s)")
    parser.add_argument("--hold-repo", dest="hold_repo", action="append",
                        default=[], metavar="SLUG",
                        help="switch a repo off for this run, exactly as "
                             "`groom-hold-repo: <slug>` on the --post card "
                             "does — repeatable, and how a dry run with no "
                             "card to read exercises the switch (DRE-3403)")
    parser.add_argument("--no-judgement", dest="judgement",
                        action="store_false", default=True,
                        help="sequence by the rules alone and make NO model "
                             "call — today's groomer, kept byte-for-byte so "
                             "the two readings can be compared on one "
                             "population (DRE-3150)")


def _build(args) -> dict:
    """Read the lane and propose. ONE model call unless `--no-judgement`.

    `propose` only, since DRE-3338. The drain used to build through here so
    that the batch it moved was derived the same way the approved one was, and
    a read that answered differently produced a different `proposal_id` and a
    refusal — which cost a model call and a CEO approval every time a card
    joined Intake or the model phrased a 260-card census slightly differently.
    The drain reads the approved record instead, and never reaches this
    function or the model call inside it.
    """
    cards = read_population(linear_ops, args.lane)
    # The repo switch, read BEFORE the census (DRE-3403). A card the model
    # never sees cannot be ranked, cannot be given a cycle and cannot fill a
    # slot in the batch — which is the difference between this and the
    # work-in-progress cap, that stops builds after classification.
    holds = read_holds(linear_ops, args)
    on_offer = offered(cards, holds)
    if holds:
        print(f"groomer: {_plural(len(holds), 'repo')} switched off — "
              f"{', '.join(holds)}; {_plural(len(cards) - len(on_offer), 'card')} "
              f"left out of this proposal", file=sys.stderr)
    cycles = read_cycles(linear_ops)
    judgement = None
    if getattr(args, "judgement", False):
        judgement = groom_judgement.run(
            groom_judgement.census(on_offer),
            groom_context.read_pack(linear_ops))
        if judgement.problem:
            print(f"groomer: {judgement.problem}", file=sys.stderr)
        # Beside it, what the call was sized with — the number that explains a
        # short answer, and the only place it is said in plain sight until
        # DRE-3152 renders it (DRE-3259).
        print(f"groomer: the one ranked read was made with an output budget of "
              f"{judgement.output_budget} token(s)", file=sys.stderr)
        if judgement.truncated:
            print(f"groomer: the answer was cut at that budget — "
                  f"{len(judgement.unranked)} card(s) came back unranked",
                  file=sys.stderr)
        # The number a run that says `answered` owes (DRE-3331), and the raw
        # answer it was read off, kept where the workflow can pick it up.
        print(f"groomer: the ranked read ranked {judgement.ranked} of "
              f"{len(on_offer)} card(s)", file=sys.stderr)
        keep = getattr(args, "keep_answer", None)
        if keep and judgement.answer is not None:
            with open(keep, "w", encoding="utf-8") as fh:
                fh.write(judgement.answer)
            print(f"groomer: kept the ranking answer at {keep} "
                  f"({len(judgement.answer.splitlines())} line(s))",
                  file=sys.stderr)
    return propose(cards, cycles=cycles, capacity=args.capacity,
                   batch_cycles=args.batch_cycles, lane=args.lane,
                   window_days=args.window_days, judgement=judgement,
                   held_repos=holds,
                   repo_priority=tuple(p for p in args.priority.split(",") if p))


def read_holds(lops, args) -> list[str]:
    """The repos switched off for this run (DRE-3403).

    Off the `--post` card's WHOLE thread when there is a card: a hold is not
    bound to a proposal id and outlives the proposal it was written on, so the
    current answer can be the oldest comment on the card and the fifty-comment
    window would lose it. `--hold-repo` supplies the same thing on a dry run
    with no card to read, and is honoured beside a card's own markers rather
    than instead of them — a flag that silently dropped a hold standing on the
    card would be the failure this switch exists to prevent.
    """
    flags = {slug for slug in (getattr(args, "hold_repo", None) or ()) if slug}
    card = getattr(args, "post", None)
    if not card:
        return sorted(flags)
    return sorted(flags | set(held_repos(
        decision_records(lops, card, whole_thread=True))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_census = sub.add_parser("census", help="what is in the lane, by repo")
    p_census.add_argument("--lane", default="Intake")

    p_propose = sub.add_parser("propose", help="sequence the lane; write nothing")
    _shaping(p_propose)
    p_propose.add_argument("--out", help="write the proposal JSON here")
    p_propose.add_argument("--post", help="post the proposal to this card")
    p_propose.add_argument("--keep-answer", dest="keep_answer",
                           help="write the ranked read's raw answer here, for "
                                "the run artifact — only when a call answered "
                                "(DRE-3331)")
    # DRE-3712. A demonstration is not a decision: the 2026-09-04 dry run was
    # an ordinary `--post`, so what it demonstrated ended up in the CEO's Green
    # Light queue for eight days. With this, the same run renders the whole
    # page to the run log and writes nothing to the card.
    p_propose.add_argument("--dry-run", dest="dry_run", action="store_true",
                           help="post NOTHING, whatever --post names — the "
                                "proposal it would have posted goes to the run "
                                "log instead (DRE-3712)")

    # NO shaping flags (DRE-3338). The batch a drain moves is read off the
    # approved proposal record, so there is nothing left for a flag to shape —
    # and a flag that no longer shapes anything is a flag somebody will pass a
    # different value to and expect a different batch. `--lane` survives only as
    # the fallback for a record whose own lane line could not be read.
    p_drain = sub.add_parser("drain", help="move the APPROVED batch onward")
    p_drain.add_argument("--card", required=True,
                         help="the card the proposal and its approval live on")
    p_drain.add_argument("--lane", default="Intake",
                         help="the lane the batch is moved out of, when the "
                              "record does not name one (default %(default)s)")

    args = parser.parse_args(argv)

    if args.command == "drain":
        try:
            result = drain(linear_ops, card=args.card, lane=args.lane)
        except (DrainRefused, WillNotClose, ValueError) as e:
            print(f"groomer: refused — {e}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "census":
        cards = read_population(linear_ops, args.lane)
        print(json.dumps({"lane": args.lane, "population": len(cards),
                          "by_repo": census(cards)}, indent=2))
        return 0

    proposal = _build(args)
    # ONE read of the thread, and only when there is a card to read: it answers
    # both "is there a decline to open with" (DRE-3373) and "is this batch
    # already proposed here". Before `--out`, so the artifact the console reads
    # carries the answer the comment does.
    records = decision_records(linear_ops, args.post) if args.post else None
    if records is not None:
        answer_decline(proposal, records)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(proposal, fh, indent=2)
        print(f"wrote {args.out} ({proposal['id']})")
    # A dry run reads everything a real one reads — the lane, the cycles, the
    # thread — and writes none of it (DRE-3712). Said out loud, because the
    # page below it is otherwise indistinguishable from one on the card.
    if args.post and getattr(args, "dry_run", False):
        print(f"dry run — nothing posted to {args.post}; the proposal it "
              "would have posted follows")
    elif args.post:
        post_proposal(linear_ops, args.post, proposal, records)
    print(render_proposal(proposal))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
