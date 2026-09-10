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
  4. **Sequences.** Urgent first, then High, then everything created inside the
     window — newest first — subject to the constraints above, deterministically.
     Repo order is a tie-break inside a band and never the master key
     (DRE-3096).
  5. **Assigns cycles.** Linear's own primitive — cycles are enabled and cycle
     11 is running, so "which cycle" is expressible today without inventing a
     container.
  6. **Proposes.** The batch, its order and the WHY on every row, what is
     deferred and what brings each one back, what is recommended dead and on
     whose word, what could not be ranked at all, and — said out loud rather
     than discovered — which repos wait and roughly how long (DRE-3152).

## The order, top to bottom (DRE-3096)

  1. **Urgent** (Linear priority 1) opens the batch, every repo, newest first.
     The production-issue lane: a card raised while debugging goes ahead of
     everything.
  2. **High** (2) next, newest first — otherwise High means nothing.
  3. **Then the window**: created in the last `WINDOW_DAYS` days, newest first.
  4. **Repo order is a tie-break inside a band** — Portico first only among
     cards of equal priority created on the same day.
  5. **Older than the window is "not now" by default.** Those cards stay in
     Intake ungroomed, reported as one line rather than aged out, cancelled or
     moved. `INTAKE_HOLD` semantics (DRE-3035) are untouched.
  6. **Two things still pull an old card forward**: a file collision with a
     batched card, and being a Linear blocker of one.
  7. **The date is the CREATION date**, never the last update. A stray agent
     comment must not bump a card; the way to resurrect an old one is to raise
     its priority, which is a deliberate human act.

The epic is still the unit, so an epic's band is the highest priority and the
newest creation among the epic and its children — one Urgent child pulls the
whole unit into the batch.

## Three outcomes, and only the first one moves

`now`, `not-now`, `dead`. **"Not now" is first-class**: a card can be
well-formed, wanted, and correctly left alone for a month, and without a "later"
the only way to say it is to say "no". **"Dead" is a recommendation and never an
action** — cancelling is destructive and belongs to the operator. Every dead
recommendation names the card or merged PR that replaced it, because a
recommendation nobody can check is one nobody should act on. In the 2026-08-22
sweep the recommendation, the decision and the execution were three separate
steps, and the executing agent caught an error in its own brief precisely
because it was working from an explicit list rather than its own judgement.

## The approval gate

`propose` writes nothing but the proposal comment `--post` asks for, and writes
that one at most once: it reads the card first and skips a proposal already
there, so a retried run adds no duplicate (vendor boundary Q3).

`drain` moves the approved batch out of Intake and into Planning, and it moves
**the batch that was APPROVED, read from the record** (DRE-3338): the approval
names a proposal id, the proposal comment carrying that id is the record, and
the cards in its batch table are the cards that move, in that order. An
approval written by the pipeline's own Linear identity is refused: a gate the
proposer can pass by itself is not a gate.

The CEO says more than yes or no (DRE-3370). Beside `groom-approved` the card
carries `groom-declined: <id> — <reason>`, `groom-excluded: <id> DRE-N` and
`groom-added: <id> DRE-N`, all read the same way — anchored at the start of the
comment, emoji optional — and all honoured only when their author is not the
pipeline. The drain moves the approved batch MINUS the exclusions PLUS the
additions, reads the WHOLE thread to find them, and writes one
`groom-drained: <id>` record of what it did; every refusal is written down as
`groom-drain-refused: <id> — <reason>`, and a batch that already carries a
drained record is refused, so a second dispatch moves nothing.

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
property is kept where it belongs: the drain reads the list the CEO saw, and
refuses if any card on it has moved since.

The cadence is D5, approved 2026-08-23: **on demand, until the groomer's
judgement has been audited.** The cost is stated rather than hidden — on demand
means it runs when someone remembers, and this programme's thesis is that
anything relying on remembering eventually does not happen. Revisit once the
calls have been checked against a real batch.

CLI:

    python3 scripts/groomer.py census  [--lane Intake]
    python3 scripts/groomer.py propose [--lane Intake] [--capacity 20]
                                       [--batch-cycles 1] [--priority portico]
                                       [--window-days 14] [--no-judgement]
                                       [--out proposal.json] [--post DRE-N]
                                       [--keep-answer judgement-answer.txt]
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
through `groom_judgement.py` — and the model's `now` set fills the batch in the
model's order.

**The rules constrain that read; they do not re-rank it.** A collision still
re-orders it, a blocker still holds, an epic is still one unit, and capacity
still caps. A card the answer omits or garbles is `unranked` and stays exactly
where the rules had it; a card the read calls `likely-done` joins the dead
recommendations with its evidence beside the regex's. Every reason passes
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
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocker_prose  # noqa: E402 — ONE anchored blocker-prose grammar (DRE-2922)
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
        "Wanted, and deliberately not this batch. Either it names the cycle it "
        "is reconsidered in, or it is older than the window and stays in Intake "
        "ungroomed — this is 'later', and it is not 'no'."
    ),
    "dead": (
        "Recommended for cancellation, and never cancelled here. Names the "
        "card or merged PR that superseded it; the operator decides and the "
        "operator executes."
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

# The lane the drain writes into: Intake's exit is a classification, and
# Planning is what produces one (DRE-2719).
DRAIN_TO = "Planning"

# Terminal lanes the drain refuses outright. The groomer recommends; it does
# not cancel. Stated as data so the refusal is testable rather than implied by
# the absence of code.
NEVER_WRITES = ("Canceled", "Duplicate", "Done")

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

# Every marker this module writes or reads, in one tuple — the set a decline's
# reason is defanged against (DRE-3373). A reason is CEO-written free text that
# `propose` renders back into a Linear comment, and a Linear comment is exactly
# where all of these are read from.
ALL_MARKERS = (PROPOSAL_TAG, *DECISION_TAGS, DRAINED_TAG, DRAIN_REFUSED_TAG)

# The answering paragraph's opener (DRE-3373). A constant because the console
# finds the answer by this string, so a rename here is a rename there.
ANSWER_OPENER = "**Answering your decline of"

# The four outcomes a card can carry in the drain's record. Data, so the render
# and the console mirror one list rather than two spellings of it.
DRAIN_OUTCOMES = ("moved", "held back", "added", "refused")

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

# How far back the batch reaches, in days of CREATION age (CEO decision,
# 2026-09-04: "14 days, creation date"). A constant and a flag, because the
# drain of the old Backlog runs at 14 and the steady state widens to 30 — that
# is a dial, not a code change.
WINDOW_DAYS = 14

# Linear's own priority numbers. Only these two are lanes: Medium (3) and Low
# (4) are ordinary cards, and reading them as bands would make "High" mean
# nothing again.
URGENT = 1
HIGH = 2

# The bands, in the order they are applied. A unit's band is the whole of its
# rank's first element, so a band is never mixed with another one — the window
# cannot outrank Urgent however fresh it is.
BAND_URGENT = 0
BAND_HIGH = 1
BAND_WINDOW = 2
BAND_OLDER = 3
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


# The operator's switch on the whole lane (DRE-3035), read once at import. The
# drain and the sweep's age-out are the two things that move a card out of
# Intake, and they read it from one place — a switch two readers interpret
# separately is a pen with a hole in it. `propose` is untouched: it writes
# nothing but a comment, and a held pen still wants a batch prepared for the
# day it opens.
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


class NotInLane(DrainRefused):
    """A card the drain would move is no longer in the lane it was approved out
    of — somebody moved it by hand since, or an addition names a card that has
    already left. Raised BEFORE any write, for the WHOLE batch: an approved
    order half-executed is an order nobody gave."""


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


class WillNotCancel(RuntimeError):
    """The drain was pointed at a terminal lane. The groomer recommends and
    never cancels — cancelling is the operator's, as a separate step.

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

def units(cards: list[dict], *, now: str | None = None,
          window_days: int = WINDOW_DAYS) -> list[dict]:
    """Cards grouped into the things a cycle is filled with: an epic with all
    of its children present, or a single parentless card.

    Each unit carries the band it is sequenced in. The epic is the atom, so the
    band is read across the whole unit — the highest priority and the newest
    creation among the epic and its children — and one Urgent child therefore
    pulls its epic's unit into the batch (DRE-3096).
    """
    now = now or _now()
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
        newest = max(_created(c) for c in members)
        out.append({
            "key": key,
            "epic": epic,
            "repo": repo,
            "created": min(_created(c) for c in members),
            "newest": newest,
            "priority": priority,
            "band": _band(priority, newest, now, window_days),
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


def _band(priority: int, newest: str, now: str, window_days: int) -> int:
    """Which of the four bands a unit sequences in."""
    if priority == URGENT:
        return BAND_URGENT
    if priority == HIGH:
        return BAND_HIGH
    return BAND_WINDOW if _within_window(newest, now, window_days) else BAND_OLDER


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
    heap = [(rank(k), k) for k in keys if not incoming[k]]
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
            heapq.heappush(heap, (rank(stuck), stuck))
            continue
        _, key = heapq.heappop(heap)
        if key not in remaining:
            continue
        remaining.discard(key)
        order.append(key)
        for nxt in sorted(outgoing[key]):
            incoming[nxt].discard(key)
            if not incoming[nxt] and nxt in remaining:
                heapq.heappush(heap, (rank(nxt), nxt))
    return order


def sequence(cards: list[dict], *, collisions: dict | None = None,
             repo_priority=REPO_PRIORITY, broken: list | None = None,
             now: str | None = None, window_days: int = WINDOW_DAYS,
             verdicts: dict | None = None) -> list[dict]:
    """The population as ONE order: unit before unit, card before card.

    Urgent first, then High, then the last `window_days` of creation — newest
    first, and the repo only breaks a tie within a band (DRE-3096). Subject to
    the constraints throughout, so a card another card's work collides with is
    pulled forward rather than silently scheduled beside it.

    Every card comes out with a position, including the ones older than the
    window: the order is over the whole population. Those rows carry
    `deferred: True`, which is what keeps them out of the batch and out of the
    cycle assignment — they stay in Intake, ungroomed.

    With `verdicts` (DRE-3150) the model's `now` set fills the batch **in the
    model's order** and the bands become the tie-break beneath it. Everything
    below this line is unchanged, and that is the point: the constraints decide
    what is POSSIBLE and the ranking decides what happens first among the
    possible — so a collision still re-orders the model, a blocker still holds,
    an epic is still one unit, and capacity still caps. **The rules constrain
    the read; they do not re-rank it.**
    """
    collisions = collisions if collisions is not None else collision_report(cards)
    by_id = {c["identifier"]: c for c in cards}
    unit_list = units(cards, now=now, window_days=window_days)
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
    model_index = _model_index(verdicts, unit_of)
    batchable = _batchable(unit_list, unit_edges, verdicts=verdicts)

    def unit_rank(key):
        # The band first and the repo LAST: Portico separates two cards of the
        # same priority created on the same day, and decides nothing else. The
        # day is the granularity the tie-break is defined at, so the timestamp
        # only orders cards the day cannot separate.
        unit = unit_index[key]
        base = (unit["band"], -_day_ordinal(unit["newest"]), ranks[unit["repo"]],
                -_epoch(unit["newest"]), _card_sort_key(key))
        if model_index is None:
            return base
        # The model's position for this unit, and the bands underneath it as
        # the tie-break for everything it did not rank `now`. A unit with no
        # `now` card sorts after every unit that has one, in today's order.
        return (model_index.get(key, len(model_index)),) + base

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


def _model_index(verdicts: dict | None, unit_of: dict) -> dict | None:
    """`{unit key: the model's position for it}`, or None with no judgement.

    A unit's position is its EARLIEST `now` card, so an epic whose third child
    the model ranked first goes where that child went — the epic is the atom of
    cycle assignment and the model does not get to split it.
    """
    if verdicts is None:
        return None
    index: dict = {}
    for identifier, verdict in verdicts.items():
        if getattr(verdict, "outcome", None) != "now":
            continue
        key = unit_of.get(identifier)
        if key is not None and key not in index:
            index[key] = len(index)
    return index


def _batchable(unit_list: list[dict], unit_edges: set[tuple[str, str]], *,
               verdicts: dict | None = None) -> set[str]:
    """The units the batch may contain: everything inside the window, plus what
    those units need to go first.

    The two things that still pull an old unit forward are the two constraints
    the sequence already carries — a file collision with a batched card, and
    being a Linear blocker of one. Both are edges here, so the pull is
    transitive: a card that blocks a card that collides with a batched card is
    in the batch too, which is the only order that does not leave a conflict
    behind.

    With a judgement (DRE-3150) the model's `now` set is what the batch is made
    of. A unit the model ranked `not-now` is out — that is the answer it gave —
    and a unit it said nothing usable about falls back to today's window rule,
    because an `unranked` card stays exactly where the rules had it.
    """
    if verdicts is None:
        keep = {u["key"] for u in unit_list if u["band"] != BAND_OLDER}
    else:
        keep = set()
        for unit in unit_list:
            outcomes = {verdicts[c].outcome for c in unit["cards"]
                        if c in verdicts}
            if "now" in outcomes:
                keep.add(unit["key"])
            elif not (outcomes - {"unranked"}) and unit["band"] != BAND_OLDER:
                keep.add(unit["key"])
    predecessors: dict[str, set[str]] = {}
    for before, after in unit_edges:
        predecessors.setdefault(after, set()).add(before)
    frontier = list(keep)
    while frontier:
        for before in predecessors.get(frontier.pop(), ()):
            if before not in keep:
                keep.add(before)
                frontier.append(before)
    return keep


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
            window_days: int = WINDOW_DAYS, judgement=None) -> dict:
    """The whole population, sequenced, with one outcome per card.

    `judgement` is a `groom_judgement.Judgement` (or a bare
    `dict[str, Verdict]`) and `None` is the rules-only path this has always
    taken — `--no-judgement`, kept byte-for-byte so the audit card can run the
    two readings over one population (DRE-3150).
    """
    now = now or _now()
    verdicts = _verdicts_of(judgement)
    dead, live = [], []
    unstated = []
    for card in sorted(cards, key=lambda c: _card_sort_key(c["identifier"])):
        target = superseded_by(card.get("description"))
        if target:
            dead.append({"identifier": card["identifier"],
                         "title": card.get("title") or "",
                         "repo": repo_of(card), "superseded_by": target,
                         "source": DEAD_FROM_LINE})
            continue
        # The model's dead recommendation lands in the SAME list as the
        # regex's, with its evidence beside the other's `Superseded by:` line.
        # A card the description already condemned is not re-judged: the
        # declaration on the card is the stronger of the two, because a person
        # wrote it.
        verdict = verdicts.get(card["identifier"]) if verdicts else None
        if verdict is not None and verdict.outcome == "likely-done":
            dead.append({"identifier": card["identifier"],
                         "title": card.get("title") or "",
                         "repo": repo_of(card), "superseded_by": None,
                         "source": DEAD_FROM_JUDGEMENT})
            continue
        if supersession_gap(card.get("description")):
            unstated.append(card["identifier"])
        live.append(card)

    collisions = collision_report(live)
    broken: list = []
    ordered = sequence(live, collisions=collisions, broken=broken,
                       repo_priority=repo_priority, now=now,
                       window_days=window_days, verdicts=verdicts)
    # Only what the window admits is given a cycle. A card older than it is not
    # scheduled at all — "not now" here means ungroomed and still in Intake, not
    # "reconsidered in cycle 14", and inventing a cycle for it would say the
    # groomer had made a plan for a card it deliberately did not look at.
    rows = cycle_plan([r for r in ordered if not r["deferred"]], cycles, capacity)
    deferred = [r for r in ordered if r["deferred"]]

    batch_numbers = sorted({r["cycle"] for r in rows})[:batch_cycles]
    now_rows, later_rows = [], []
    for row in rows:
        if row["cycle"] in batch_numbers:
            now_rows.append({k: row[k] for k in
                             ("identifier", "title", "position", "cycle",
                              "cycle_id", "unit", "epic", "repo", "projected",
                              "band")})
        else:
            later_rows.append({"identifier": row["identifier"],
                               "title": row["title"], "repo": row["repo"],
                               "reconsidered_in": row["cycle"],
                               "projected": row["projected"],
                               "older_than_window": False})
    later_rows += [{"identifier": row["identifier"], "title": row["title"],
                    "repo": row["repo"], "reconsidered_in": None,
                    "projected": False, "older_than_window": True}
                   for row in deferred]

    sequence_rows = [{**r, "outcome": ("now" if r["cycle"] in batch_numbers
                                       else "not-now")} for r in rows]
    sequence_rows += [{**r, "cycle": None, "cycle_id": None, "projected": False,
                       "outcome": "not-now"} for r in deferred]
    sequence_rows.sort(key=lambda r: r["position"])
    sequence_rows += [{"identifier": d["identifier"], "title": d["title"],
                       "position": None, "unit": None, "epic": None,
                       "repo": d["repo"], "project": None, "cycle": None,
                       "cycle_id": None, "projected": False,
                       "band": None, "deferred": False, "outcome": "dead"}
                      for d in dead]

    proposal = {
        "generated_at": now,
        "lane": lane,
        "population": len(cards),
        "census": census(cards),
        "capacity": capacity,
        "cycle_days": cycle_days(cycles),
        "window_days": window_days,
        "older_than_window": {
            "days": window_days,
            "cards": len(deferred),
            "line": older_than_window_line(len(deferred), window_days),
        },
        "batch": {"cycles": batch_numbers, "cards": len(now_rows)},
        "repo_order": [r for r in _repo_rank(live, repo_priority)],
        "sequence": sequence_rows,
        "outcomes": {"now": now_rows, "not-now": later_rows, "dead": dead},
        "collisions": collisions,
        "unhonoured_constraints": broken,
        "unstated_supersessions": unstated,
    }
    proposal["deprioritised"] = _deprioritised(proposal)
    proposal["judgement"] = _annotate(proposal, judgement, verdicts,
                                      window_days=window_days)
    # LAST, and deliberately after the annotation: `proposal_id` digests the
    # batch's cards, positions and cycles and NOTHING else, so a reason that
    # reads differently on a re-run cannot retire a CEO approval of the same
    # batch (DRE-3150's contract; the same sha-binding idea the merge gate uses).
    proposal["id"] = proposal_id(proposal)
    return proposal


# --------------------------------------------------------------------------- #
# the judgement, written onto the rows                                         #
# --------------------------------------------------------------------------- #


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


def _rules_reason(outcome: str, row: dict, window_days: int) -> str:
    """Why the RULES put this card where they put it. Plain English, ours."""
    if outcome == "dead":
        return (f"its own description says it was superseded by "
                f"{row.get('superseded_by')}")
    if outcome == "now":
        band = BAND_LABELS.get(row.get("band"))
        opened = f"marked {band}" if band else "created inside the window"
        return f"in the batch by the rules — {opened}, position {row['position']}"
    if row.get("older_than_window"):
        return (f"older than the {window_days}-day window, so this pass did "
                f"not groom it")
    return (f"wanted, and this batch was full — it is reconsidered in cycle "
            f"{row.get('reconsidered_in')}")


def _rules_trigger(row: dict) -> str:
    """What brings a deferred card back. Always present, because "later" with
    no trigger is "no" wearing a softer word."""
    if row.get("older_than_window"):
        return "when somebody raises its priority to High or Urgent"
    return f"when cycle {row.get('reconsidered_in')} opens"


def _mark(outcome: str, row: dict, verdict, *, window_days: int,
          withheld: list) -> dict:
    """The four fields DRE-3150 puts on every row: reason, trigger, evidence,
    judged.

    Every one of them that a model wrote passes `_showable` first. A refused
    REASON is replaced with the contract's exact sentence and the card is
    listed in `withheld`; a refused trigger falls back to the rules' own, and
    refused evidence is dropped to None — a dead recommendation whose evidence
    cannot be shown is still reported as judged, and the run log holds the text
    nobody could put on the page.
    """
    identifier = row.get("identifier")
    # A card its OWN DESCRIPTION condemned was never placed by the read: the
    # declaration a person wrote outranks it, and the reason has to say what
    # superseded it rather than that a model could not rank it.
    if outcome == "dead" and row.get("source") == DEAD_FROM_LINE:
        verdict = None
    judged = verdict is not None and verdict.outcome != "unranked"
    reason = (verdict.reason if verdict is not None
              else _rules_reason(outcome, row, window_days))
    if not _showable(reason):
        if verdict is not None and verdict.outcome == "unranked":
            # The unranked sentence is ours and always showable; anything else
            # here is a model's words, refused.
            reason = verdict.reason
        else:
            withheld.append(identifier)
            reason = WITHHELD_REASON

    trigger = None
    if outcome == "not-now":
        trigger = verdict.pointer if (judged and verdict.outcome == "not-now") \
            else None
        if trigger is not None and not _showable(trigger):
            withheld.append(identifier)
            trigger = None
        trigger = trigger or _rules_trigger(row)

    evidence = None
    if outcome == "dead" and judged and verdict.outcome == "likely-done":
        evidence = verdict.pointer
        if not _showable(evidence):
            withheld.append(identifier)
            evidence = None

    return {"reason": reason, "trigger": trigger, "evidence": evidence,
            "judged": judged}


def _annotate(proposal: dict, judgement, verdicts: dict | None, *,
              window_days: int) -> dict:
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
                          (verdicts or {}).get(identifier),
                          window_days=window_days, withheld=withheld)
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


def older_than_window_line(count: int, window_days: int) -> str:
    """The one line the held-back cards are reported as.

    One line and not a list: the population outside the window is most of a
    200-card Intake, and a proposal that prints all of it buries the batch the
    CEO is being asked to approve. What the line has to carry is the way back
    in — raising a card's priority is a deliberate human act, and it is the
    only thing that pulls an old card into a batch.
    """
    return (f"{_plural(count, 'card')} older than {window_days} days, not "
            f"batched — raise a card's priority to High or Urgent to pull it in.")


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
        # A card older than the window has no cycle to wait for — it is
        # reported by its own one-line receipt, not as a repo that waits.
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
    """A digest of the BATCH — its cards, their order and their cycles.

    The id is what an approval names, so an approval binds to a batch the way a
    critic verdict binds to a head sha: re-run the groomer after the population
    moves and the id changes, which retires the old approval instead of letting
    it authorise a batch nobody read.
    """
    payload = json.dumps({
        "lane": proposal["lane"],
        "batch": [[r["identifier"], r["position"], r["cycle"]]
                  for r in sorted(proposal["outcomes"]["now"],
                                  key=lambda r: r["position"])],
    }, sort_keys=True)
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
    """
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
        if record.get("authored_by_pipeline"):
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

def render_proposal(proposal: dict) -> str:
    batch = proposal["outcomes"]["now"]
    cycles = ", ".join(str(n) for n in proposal["batch"]["cycles"])
    w = []
    add = w.append
    add(f"# Groom proposal `{proposal['id']}` — cycle {cycles}")
    add("")
    # The CEO's last open decline, answered before anything else on the page
    # (DRE-3373). Absent unless `answer_decline` found one, so a proposal with
    # no decline behind it renders byte for byte as it did before that card.
    if proposal.get("answering"):
        w.extend(_render_answer(proposal["answering"]))
    add(f"{len(batch)} cards of {proposal['population']} in {proposal['lane']} "
        f"are proposed for cycle {cycles}, in the order below. Nothing moves "
        f"until you approve it.")
    add("")
    # One line, before anything else, saying what did the ranking and what it
    # read — including the two things a page that stayed silent would let pass
    # for a model's opinion: a reason the guard withheld, and an answer the
    # budget cut short (DRE-3152).
    add(_receipt_line(proposal))
    add("")
    add("**To approve:** comment `" + approval_comment(proposal["id"])
        + "` on this card. Anything else — including a comment that mentions "
          "the marker — leaves the batch where it is.")
    add("")
    # The rest of the vocabulary, at the one place the CEO is deciding
    # (DRE-3370). A marker nobody is told about is a marker nobody writes.
    add(f"**To say more than yes:** `{MARK} {DECLINE_TAG}: {proposal['id']} — "
        f"<reason>` declines the batch (the reason is required); "
        f"`{MARK} {EXCLUDE_TAG}: {proposal['id']} DRE-N` holds one card back; "
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
    add(f"Urgent first, then High, then everything created in the last "
        f"{proposal['window_days']} days — newest first, whatever repo it is "
        f"in. Repo order ("
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
    if proposal["older_than_window"]["cards"]:
        add(proposal["older_than_window"]["line"])
        add("")
        add("They stay in Intake, ungroomed. Nothing ages them out, cancels "
            "them or moves them.")
        add("")
    w.extend(_render_not_now(proposal))
    add("## Recommended dead — your call, not ours")
    add("")
    if proposal["outcomes"]["dead"]:
        w.extend(_render_dead(proposal))
        add("")
        add("The groomer never cancels. Cancelling is destructive and stays "
            "yours, as a separate step.")
    else:
        add("- None.")
    if proposal["unstated_supersessions"]:
        add("")
        add("Named nothing: "
            + ", ".join(proposal["unstated_supersessions"])
            + " say they are superseded without naming what replaced them, so "
              "they are sequenced normally rather than recommended dead.")
    add("")
    w.extend(_render_unranked(proposal))
    add("## On cycles")
    add("")
    add(CYCLE_IS_NOT_SPRINT_PLANNING)
    add("")
    return "\n".join(w)


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
    no trigger at all is not listed here: the window receipt above already
    reports it, and inventing a trigger for a card nothing scheduled would say
    the groomer had made a plan for it.
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


def _render_dead(proposal: dict) -> list:
    """The dead recommendations, split by WHERE each one came from.

    Two readers propose a cancellation and they are not the same claim: a
    `Superseded by:` line is a declaration a person wrote on the card, and the
    ranked read's is a judgement with the evidence it named beside it. The CEO
    decides either way, so the page says which one is being read.
    """
    rows = proposal["outcomes"]["dead"]
    declared = [r for r in rows if r.get("source") != DEAD_FROM_JUDGEMENT]
    judged = [r for r in rows if r.get("source") == DEAD_FROM_JUDGEMENT]
    w: list = []
    if declared:
        w += ["**Declared on the card** — its own description says so:", ""]
        for row in declared:
            w.append(f"- {row['identifier']} — superseded by "
                     f"{row['superseded_by']} · {_trim(row['title'])}")
        w.append("")
    if judged:
        w += ["**Judged by the ranked read** — a call, with what it points "
              "at:", ""]
        for row in judged:
            w.append(f"- {row['identifier']} — likely done or obsolete — "
                     + (f"{row['evidence']}" if row.get("evidence")
                        else "the evidence it named was not fit to show; it "
                             "is in the run log")
                     + f" · {_trim(row['title'])}")
        w.append("")
    return w[:-1] if w else w


def _render_unranked(proposal: dict) -> list:
    """The cards the read could not rank, as their own section.

    Never folded into "not now": a card nobody could place is not a card
    deliberately deferred, and a refusal that renders as a deferral is a
    refusal nobody ever reads. The rules kept each of these exactly where they
    had them — what is owed is a person, not a cycle.
    """
    block = proposal.get("judgement") or {}
    if not block.get("enabled") or not block.get("unranked"):
        return []
    titles = {row["identifier"]: row.get("title") or ""
              for row in proposal["sequence"]}
    w = ["## Could not rank — needs a person", ""]
    w.append(f"{_plural(len(block['unranked']), 'card')} the read could not "
             f"place. They stayed exactly where the rules had them, and each "
             f"one wants a human answer rather than another pass.")
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
    per_card: list[tuple[str, str, str, str | None]] = []   # tag, pid, card, why
    ignored: list[dict] = []
    drained: list[str] = []

    for index, record in enumerate(records):
        body = record.get("body") or ""
        mine = bool(record.get("authored_by_pipeline"))

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
                # add anything.
                if tag == APPROVAL_TAG:
                    by_pipeline_approval = True
                else:
                    ignored.append(_ignored(
                        tag, f"`{tag}` {_BY_THE_PIPELINE}",
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
                                          by_pipeline_approval)
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
                      by_pipeline_approval: bool) -> tuple[str | None, str | None]:
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
        return None, ("the only approval on this card was written by the "
                      "pipeline's own Linear identity — the proposer cannot "
                      "approve its own proposal")
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


# The proposal's own heading, and the one line that names the lane. Both are
# written by `render_proposal` a few dozen lines up, and read back here: the
# render and this parser are two halves of ONE contract, so the round trip is
# asserted in tests/test_groomer_approval_gate.py rather than assumed.
_PROPOSAL_HEADING = re.compile(
    r"^#\s+Groom proposal\s+`([0-9a-f]{6,})`\s+—\s+cycle\s+(.*)$", re.M)
_LANE_LINE = re.compile(
    r"^\d+\s+cards?\s+of\s+\d+\s+in\s+(.+?)\s+are proposed for cycle\b", re.M)
_BATCH_HEADING = "## The batch, in order"
_BATCH_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(DRE-\d+)\s*\|")


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
    """
    text = body or ""
    marker = _PROPOSAL_LINE.match(text.strip())
    if not marker:
        return None
    heading = _PROPOSAL_HEADING.search(text)
    lane = _LANE_LINE.search(text)
    batch = []
    inside = False
    for line in text.splitlines():
        if line.strip() == _BATCH_HEADING:
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if not inside:
            continue
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
    return {
        "id": marker.group(1),
        "lane": lane.group(1).strip() if lane else None,
        "cycles": [int(n) for n in re.findall(r"\d+", heading.group(2))]
                  if heading else [],
        "batch": sorted(batch, key=lambda r: r["position"]),
    }


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
    """Move the APPROVED batch onward — minus every exclusion, plus every
    addition — in the order the record carries.

    The batch is READ, never re-derived (DRE-3338): the approval names an id,
    the proposal comment carrying that id is the record, and the cards in its
    batch table are the cards that move. The CEO's per-card decisions
    (DRE-3370) are read off the same thread and adjust that list: an excluded
    card stays where it is, an added card moves after the batch on the batch's
    own cycle. No model is called and the population is never re-read — a drain
    is a move, not a judgement.

    Refuses, before any card moves: a closed pen, a terminal destination, a
    missing / pipeline-written / declined approval, an approval whose proposal
    is not on the card, a batch already drained, a cycle Linear does not carry,
    and any card it would move that is not in the lane. Every refusal but the
    terminal destination is written onto the card as
    `🧺 groom-drain-refused: <id> — <reason>`.
    """
    if to in NEVER_WRITES:
        # Before the card is read, so there is no batch to name and nothing to
        # write down: a bad invocation, not a refused batch.
        raise WillNotCancel(
            f"the drain will not write {to!r}: the groomer recommends and never "
            f"cancels, and cancelling stays the operator's own step")
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
    nothing can read.
    """
    # Reads only, and they decide nothing yet: the hold below has to be able to
    # say how big the batch behind the pen is, and the only place that number
    # exists now is the record itself. The WHOLE thread, paginated: a proposal
    # card carries one comment per per-card decision, and the approval is the
    # OLDEST of them — the first thing to fall out of a fifty-comment window.
    records = lops.comment_records(card, whole_thread=True)
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
    plan = _drain_plan(record, decisions, lane=lane)

    # Every card's CURRENT lane, before anything moves. A card somebody moved by
    # hand since the approval is not the card the CEO approved a move for, and
    # an addition naming a card that has already left the lane is not the card
    # the CEO meant either. Either way the answer is to refuse the WHOLE batch
    # rather than to move the part that still fits: an approved order
    # half-executed is an order nobody gave.
    issues, gone = {}, []
    for row in plan["moving"]:
        issues[row["identifier"]] = issue = lops.get_issue(row["identifier"])
        now = ((issue or {}).get("state") or {}).get("name")
        if now != lane:
            gone.append((row["identifier"],
                         now or "a lane this run could not read"))
    if gone:
        named_cards = ", ".join(f"{i} ({where})" for i, where in gone[:5])
        raise NotInLane(
            f"{_plural(len(gone), 'card')} the drain would move left {lane} "
            f"after batch {record['id']} was approved — {named_cards} — so "
            f"nothing moved; re-propose and get the new batch approved",
            batch=record["id"])

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

    result = {"moved": moved,
              "held_back": [r["identifier"] for r in plan["held_back"]],
              "added": [r["identifier"] for r in plan["moving"]
                        if r["outcome"] == "added"],
              "refused": plan["ignored"],
              "rows": plan["rows"], "to": to, "from": lane,
              "cycle": cycle[0], "proposal": record["id"]}
    lops.cmd_comment(card, drained_record(result))
    return result


def _drain_plan(record: dict, decisions: dict, *, lane: str) -> dict:
    """The batch minus the exclusions plus the additions, and the table row
    every card involved gets.

    Rows follow the PROPOSAL's own order first, so a reader can lay the record
    beside the proposal and go down both together; then the additions, in the
    order the CEO wrote them; then any decision the drain would not honour.
    """
    excluded, added = decisions["excluded"], dict(decisions["added"])
    in_batch = {row["identifier"] for row in record["batch"]}
    ignored = list(decisions["ignored"])

    # An exclusion can only take a card OUT of the batch. One naming a card
    # that was never in it holds nothing back — and reading it as a decision
    # would let a marker invent a card into a record it is not in.
    for identifier, mark in excluded.items():
        if identifier not in in_batch:
            ignored.append(_ignored(
                mark["tag"],
                f"`{EXCLUDE_TAG}` names a card that is not in the approved "
                f"batch, so there was nothing to hold back", identifier))

    moving, held_back, rows = [], [], []
    for row in record["batch"]:
        identifier = row["identifier"]
        mark = excluded.get(identifier)
        if mark:
            held_back.append(row)
            rows.append({"identifier": identifier, "outcome": "held back",
                         "why": _marker_why(mark)})
            continue
        if identifier in added:
            # Already in the batch: the addition asks for something that is
            # happening anyway, so it is reported rather than acted on.
            ignored.append(_ignored(
                added.pop(identifier)["tag"],
                f"`{ADD_TAG}` names a card already in the approved batch",
                identifier))
        moving.append({**row, "outcome": "moved"})
        rows.append({"identifier": identifier, "outcome": "moved",
                     "why": f"proposal `{record['id']}` position "
                            f"{row['position']}"})

    for identifier, mark in added.items():
        moving.append({"identifier": identifier, "position": None,
                       "outcome": "added"})
        rows.append({"identifier": identifier, "outcome": "added",
                     "why": _marker_why(mark)})

    for mark in ignored:
        if mark["identifier"] and mark["identifier"] in in_batch:
            # The card has a row already, carrying what actually happened to
            # it. The dropped marker is named there rather than contradicted by
            # a second row for the same card.
            row = next(r for r in rows if r["identifier"] == mark["identifier"])
            row["why"] += f" — {mark['why']}, so it was ignored"
            continue
        rows.append({"identifier": mark["identifier"] or "—",
                     "outcome": "refused", "why": mark["why"]})
    return {"moving": moving, "held_back": held_back, "rows": rows,
            "ignored": ignored, "lane": lane}


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
    """What the drain did, per card, on the proposal card (DRE-3326, DRE-3370).

    ONE record per drain, in a fixed grammar the console mirrors: the marker,
    then the summary line, then a row per card. Undoing a bad batch means
    knowing which cards THIS drain took and which it did not, and a run log is
    not on the card.

    The counts answer four different questions and none of them substitutes for
    another: `moved` is the approved batch that went, `held back` is what the
    CEO excluded and is still in the lane, `added` is what the CEO reached in
    for, and `refused` counts the DECISIONS the drain would not honour — a
    marker the pipeline wrote, a decline with no reason, an exclusion naming a
    card that was never in the batch. That last count is of decisions and not
    of rows, because a refused decision about a card that moved anyway is named
    on that card's own row: no card gets two.
    """
    pid = result["proposal"]
    rows = result["rows"]
    counts = Counter(row["outcome"] for row in rows)
    w = [f"{MARK} {DRAINED_TAG}: {pid}", "",
         f"moved: {counts['moved']} · held back: {counts['held back']} · "
         f"added: {counts['added']} · refused: {len(result['refused'])} → "
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


def _within_window(created: str, now: str, window_days: int) -> bool:
    """Was this created in the last `window_days`?

    A card with no readable creation date reads as OUTSIDE the window: the
    consequence is that it stays in Intake, which is the reversible answer.
    """
    moment, anchor = _moment(created), _moment(now)
    if moment is None or anchor is None:
        return False
    return moment >= anchor - timedelta(days=window_days)


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


def _cell(text: str | None, width: int = 60) -> str:
    """One markdown table cell. A pipe inside it would end the column early and
    silently shift every cell after it, and this text is written by a model."""
    return _trim((text or "—").replace("|", "\\|"), width)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _shaping(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lane", default="Intake")
    parser.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY)
    parser.add_argument("--batch-cycles", type=int, default=1)
    parser.add_argument("--priority", default=",".join(REPO_PRIORITY),
                        help="comma-separated repo slugs, highest first — a "
                             "tie-break inside a band, not the master key")
    parser.add_argument("--window-days", type=int, default=WINDOW_DAYS,
                        help="how far back the batch reaches, in days of "
                             "creation age (default %(default)s)")
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
    cycles = read_cycles(linear_ops)
    judgement = None
    if getattr(args, "judgement", False):
        judgement = groom_judgement.run(
            groom_judgement.census(cards), groom_context.read_pack(linear_ops))
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
              f"{len(cards)} card(s)", file=sys.stderr)
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
                   repo_priority=tuple(p for p in args.priority.split(",") if p))


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
        except (DrainRefused, WillNotCancel, ValueError) as e:
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
    records = linear_ops.comment_records(args.post) if args.post else None
    if records is not None:
        answer_decline(proposal, records)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(proposal, fh, indent=2)
        print(f"wrote {args.out} ({proposal['id']})")
    if args.post:
        post_proposal(linear_ops, args.post, proposal, records)
    print(render_proposal(proposal))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
