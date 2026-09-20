#!/usr/bin/env python3
"""The two plan critics — one before the CEO reads a plan, one after (DRE-2721).

Two passes asking DIFFERENT questions. If they asked the same one the second
would be waste, and the difference is what the plan IS at each moment:

  pre   — reviews a MOVING document, before the CEO has spent any attention on
          it. "Is this fit to take the CEO's time?" It protects attention and
          cannot do more than that, because intent is not settled yet.
  post  — reviews a FROZEN one, after approval. "Given this is now the
          specification, what is missing?" An adversarial pass is only worth
          much against a fixed target, and before approval there isn't one.

...and a THIRD moment, which is the same first critic reading a different kind
of card (DRE-3041):

  one-off — reviews a card that owes no plan at all, between the shape stamp
          and the move to the build queue. "Is this one pull request of work an
          agent can build unattended, with nothing in it that is a decision?"

That is a stage, not a third critic: same agent, same brief, same ladder, same
result grammar and the same round record. An epic is read twice before its
children are built; a one-off was read by NONE — its exit is mechanical
(DRE-2844), so the only judgement on it was the shape stamp, and then an
engineer was dispatched. The 2026-09-03 probes showed the cost: a business
decision stamped `one-off` was routed FLEET and would have been built.

This module is the mechanical half of both: the stage charters the prompts are
built from, the result-file grammar, the round bound, the durable markers the
send-back rate and the collision counters are read out of, and the cheap
structural checks the first critic runs before it spends a turn thinking.

Pure functions over strings with one thin CLI seam — no Linear client, no
GitHub calls — so plan.yml, the scenario walk and the tests all run the same
code. Linear I/O stays in `linear_ops.py`, whose output this module reads on
stdin (`dump-comments`, `epics-in-flight`).

Three rules baked in, each one bought:

  * THE BOUND. Two failed rounds at either critic and the plan reaches the CEO
    regardless, with the critic's stated reason attached. An unbounded loop is
    how 17 cards sat in a lane for 27 days. The budget is per planning ATTEMPT,
    counted from the `plan-cycle:` boundary the plan route writes — a
    re-planned epic gets its revision round back, because the plan the earlier
    rounds argued about no longer exists. AFTER APPROVAL the same reasoning
    holds INSIDE an attempt (DRE-4115): every send-back is followed by a
    re-plan, so the round after it reads a different plan, and "two failed
    rounds" means two rounds whose findings STILL STAND — the critic is shown
    the previous round's findings, says which the revision left open
    (`still-open:`), and a round that answered everything is not counted
    against the next. A plan that keeps producing NEW findings still parks
    after MAX_ROUNDS answered revisions, and a PERSON re-running a parked
    review opens a fresh attempt (`opens_fresh_attempt`) — every reset costs
    a human act, so nothing circles forever. Before this, DRE-3778 was
    approved five times and parked five times on "round 6 of 2".
    ON THE ONE-OFF ROUTE the same count
    holds over the card's whole history (DRE-4058): its loop runs through the
    CEO — park, answer, back to Planning, a fresh single call — so the bound is
    spent in his queue rather than in one job, and at it the card is asked for a
    REWRITE naming every finding raised so far instead of a sixth answer.
    DRE-3879 went round five times before anything counted.
  * A CRASH IS NOT A REJECTION (standards/console-honesty.md rule 1). A critic
    that produced no result did not decide anything, and must never be the
    reason a plan stops moving.
  * NOTHING HERE IS A MERGE CREDENTIAL (standards/untrusted-content.md). The
    merge gate reads verdicts out of comments, so these markers deliberately
    share no prefix with one, and every reason an agent writes is collapsed to
    a single line before it can reach `$GITHUB_OUTPUT`.
  * THE POST MARKER IS WHAT RELEASES THE CHILDREN (DRE-3059). "Only then are
    the children promotable" is half of DRE-2721's sentence and it had no
    reader: `reconcile.promote_ready()` released a child on its epic's LANE,
    so the fifteen-minute sweep promoted two of them eighty-two seconds after
    an approval that no second critic had reviewed. `post_release` and
    `promotion_refusal` below are that reader, and the sweep is the only
    promoter — the activate route runs it rather than promoting itself.
  * ...BUT THE MARKERS ARE THIS GATE'S OWN CREDENTIAL, so a record has to be
    narrower than a line of text somebody wrote. Two conditions, and both are
    required (`trusted_bodies` + `_sole_record`): the pipeline itself wrote the
    comment, AND the comment says nothing but the record. Identity alone is not
    enough — the shared Linear key also posts the planner's own plan write-up
    to the same epic, freeform prose derived from untrusted card text, and one
    line inside it matching a marker spent a round nobody ran.

CLI:
  charter <stage> [--sight-file F] [--prior-file F]
                                     the stage's prompt block; `--prior-file`
                                     is the previous round's findings block
                                     (`prior-round`), post stage only
  prior-round --stage S [--epic E]   the previous send-back round's findings
                                     as the block the charter carries, or
                                     nothing when this is the first round;
                                     comment thread on stdin (DRE-4115)
  activate-cycle --epic E [--reason R]
                                     `open` when this ACTIVATE run should open
                                     a fresh planning attempt — a PERSON
                                     re-running a review the bound parked —
                                     else `keep`; thread on stdin. Never exits
                                     non-zero: an unreadable thread keeps the
                                     attempt, the direction that parks
  mechanical [--plan-comment-file F] [--surfaces-dir D] [--note-file F]
                                     cards on stdin (`children-json`); the note
                                     is the list posted to the epic BEFORE the
                                     critic reads it. Since DRE-3079 that list
                                     includes the SPLIT LEDGER's answer: a
                                     child whose declared footprint lands on a
                                     row that died, or that carries a tell the
                                     ledger has watched kill cards, is a
                                     finding citing the row. Since DRE-3243 it
                                     also prints every card's STATE and names a
                                     DELIVERED child as a non-finding, so a
                                     Done card's work already being on `main`
                                     cannot be re-raised as a gap.
  decide --stage S --result-file F [--epic E] [--github-output F]
         [--note-file F] [--record-file F] [--escalation-file F]
                                     comment thread (JSON array) on stdin,
                                     from `dump-comments --with-authors`.
                                     The note and the record are TWO comments.
                                     `--escalation-file` is the one-off stage's
                                     CEO-facing reason, written only when the
                                     card does not pass — the critic's question
                                     below the bound, and at it the request to
                                     REWRITE the card, naming every finding the
                                     card has collected (DRE-4058).
  sight --this <EPIC>                epics in flight (JSON array) on stdin
  cycle-start --epic <EPIC> [--record]
                                     the note that opens a planning attempt,
                                     and (--record) the boundary line itself —
                                     again two comments, never one
  rate --stage S                     comment thread on stdin
  collisions                         comment thread on stdin
  late-collision --epic E --with E2 --detail "…"   print the marker line
  review-turns --execution-file F --ceiling M --children K --model X
                                     what one post-approval review SPENT
                                     against what it was given, as one line
                                     for its own comment (DRE-3498). A fact
                                     about a call: no verdict, no act, no
                                     round. Never exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checkbox_marks
import design_parity
import execution_result
import plan_footprint

# `scripts/design_parity.py already implements part of this ... Reuse it; do
# not reinvent it.` Re-exported by NAME, not re-implemented: a surface is
# accounted for only by a card's `**Design:**` ref or an explicit
# `deferred: <surface> — <reason>` line, and that definition lives once.
unaccounted_surfaces = design_parity.unaccounted_surfaces

# --- The two stages ---------------------------------------------------------

STAGE_PRE = "pre"
STAGE_POST = "post"
# The third MOMENT, on the route that owes no plan (DRE-3041). Not a third
# critic: `STAGES[STAGE_ONE_OFF]["agent"]` is AGENT_PRE, so nothing new lands in
# agents.yaml or config/models.yaml and the two routes share one reader.
STAGE_ONE_OFF = "one-off"

# The roster/ladder names for the two agents (agents.yaml, config/models.yaml).
# Also the role strings plan.yml passes to `model_fallback.py select`, and the
# assemble_context.py roles whose standards sets differ on purpose.
AGENT_PRE = "plan-critic-pre"
AGENT_POST = "plan-critic-post"

# The bound, on both loops. Two FAILED rounds — a round the critic passed is
# not a failure and a round it crashed on was never a decision.
MAX_ROUNDS = 2

# What a critic may write.
PASS = "PASS"
SEND_BACK = "SEND_BACK"
# ...and what the run reads when it wrote nothing usable. Deliberately its own
# value rather than a third verdict: "did not decide" and "decided no" are
# different facts with different next actions.
NO_RESULT = "NO_RESULT"

# The first line of the critic's result file. NOT `VERDICT:` — that string is
# an approval credential the merge gate reads, and no plan critic may mint one.
RESULT_PREFIX = "PLAN-CRITIC:"

# The durable record, posted to the epic. Lowercase and distinct from
# RESULT_PREFIX so a marker can quote a result line without becoming one.
MARKER_PREFIX = "plan-critic:"

# The boundary between one planning CYCLE and the next, posted by plan.yml the
# moment an epic is routed to plan — the first attempt and every RE-plan alike.
# The bound is "two failed rounds at this critic ON THIS ATTEMPT", never over
# the epic's lifetime: an epic sent back to Triage is re-planned from scratch,
# and a fresh plan counted against a budget the previous attempt already spent
# would be pushed to the CEO on its FIRST send-back with no revision round at
# all — reading exactly like a normal pass.
CYCLE_PREFIX = "plan-cycle:"

# A collision found AFTER the cards reached Backlog — the D3 tripwire. Counted
# in its own bucket, never mixed with the ones the post critic caught, because
# the ratio between them is the signal that the check has to split back out
# into its own pass.
LATE_COLLISION_PREFIX = "plan-collision-late:"

# A review that DIED — ended without reaching its decision — records that on
# the epic too (DRE-3241), under its own prefix so `_MARKER` can never read a
# death as a round. Nothing about it is a verdict.
DEATH_PREFIX = "plan-critic-died:"

# The lanes an epic occupies while it is in flight (config/lane-contract.json).
# What the post critic can see is exactly this, and its charter says so.
IN_FLIGHT_EPIC_STATES = ("Green Light", "Todo", "In Progress")

# --- What a CHILD's lane means to a critic (DRE-3243) -----------------------
#
# A critic is handed each child's text and the repository, and with nothing
# else it reads the text as an instruction and the tree as evidence. DRE-3164's
# round 2 sent a sound plan back for "DRE-3210's entire deliverable already
# exists, fully implemented, on main — the card asks an agent to build
# already-shipped work". DRE-3210 was DONE: built, reviewed, merged (#2308) and
# closed by that merge the evening before. A Done card describing the work it
# delivered is the normal shape of every Done card. That send-back was the
# second of two, so the epic hit the bound and parked with `needs-human` on a
# finding that was not a gap.
#
# Matched case-insensitively on the lane's own name, so a board that renders
# "done" or "DONE" reads the same. `cancelled` is here beside `canceled`
# because the terminal lane is spelled one way on this board and the other in
# half the English-speaking world, and a spelling is not a reason to re-raise a
# false finding.

#: A child in one of these lanes is DELIVERED or DROPPED — never to-build.
DELIVERED_CHILD_STATES = ("done", "canceled", "cancelled", "duplicate")

#: A child in one of these has a run or a pull request in flight: it is judged
#: on what it will LAND, not on whether its files are in the tree yet.
IN_FLIGHT_CHILD_STATES = ("in progress", "in review")


def child_state(card: dict) -> str:
    """The card's lane as the board spells it, or `""` when the record carries
    none. Empty is UNKNOWN, never "to build" — the two are told apart by every
    reader below (standards/console-honesty.md rule 2)."""
    return str((card or {}).get("state") or "").strip()


def shipped_work_is_a_finding(card: dict) -> bool:
    """Whether *"its deliverable already exists on `main`"* stands as a finding
    against this card.

    False for a delivered or dropped child and true for everything else,
    UNKNOWN included: a record with no state is a card nothing has excused, and
    excusing it would suppress the real finding the critic exists to make.
    """
    return child_state(card).lower() not in DELIVERED_CHILD_STATES


def _in_states(cards: list[dict], states) -> list[tuple[str, str]]:
    return [(c.get("identifier"), child_state(c)) for c in cards or []
            if child_state(c).lower() in states]


def delivered_children(cards: list[dict]) -> list[tuple[str, str]]:
    """`(identifier, state)` for every child that is delivered or dropped."""
    return _in_states(cards, DELIVERED_CHILD_STATES)


def in_flight_children(cards: list[dict]) -> list[tuple[str, str]]:
    """`(identifier, state)` for every child with a run or a PR out."""
    return _in_states(cards, IN_FLIGHT_CHILD_STATES)

# --- The post-approval review's turn ceiling (DRE-3241) ---------------------
#
# Sized from the plan, in one place, the way DRE-2924 sizes the QA critic's
# from the diff. THE MEASUREMENT: DRE-3164, 2026-09-05 PT. Round 1 of the
# second critic finished in 30 turns on 14 cards ($1.16, 315 s). The re-plan
# ran, the CEO approved again, and round 2 — 15 cards plus the re-plan's edits
# to re-verify — died `error_max_turns` at turn 41 of a 40-turn ceiling, twice
# (the medic's retry identically: $1.73, 368 s, zero permission denials).
# Cost per turn was flat between the rounds (~$0.039 → ~$0.042); a loop
# re-reading one file shows up as climbing per-turn cost from context growth,
# and there was none. The reading was linear and the ceiling had no headroom.
#
# The transcript itself is hidden ("full output hidden for security", held
# that way by tests/test_execution_failure_detail.py), so this is read off the
# result blocks, not the turns. Base + per-card: fifteen cards get 100 — 2.5x
# the wall round 2 hit, over 3x the round that finished.
#
# THE WHOLE BAND MOVED UP WITH THE WEB GRANT (DRE-2785): base 20 → 30, floor
# 40 → 60, cap 120 → 140. This critic reads the approved plan as the
# specification agents will build from and asks what is missing — and since the
# grant, "is that true of the vendor" is a question it can go and ANSWER rather
# than recall. A search and a fetch per external claim is turns this budget was
# never measured against; every number here was read off a critic that could
# not leave the repository. The SHAPE is untouched: the default is still the
# fifteen-card number, the floor is still the smallest budget a review gets,
# and the cap is still the QA critic's own retry ceiling — which moved to 140
# in the same change (scripts/pr_size_strategy.py).
#
# ...AND THE BASE MOVED AGAIN, 30 → 40 (DRE-3498), on the first measurement
# taken at the SMALL end. agent-bureau run 34144302622 (2026-09-07 PT, the
# post-approval review of DRE-3257, 7 cards, claude-sonnet-5) needed 51 turns
# and was cut off at the pre-grant ceiling of `20 + 4 × 7 = 48`. Seven cards
# sized to 58 and floored to 60 — under 1.2× a number measured on a critic
# that still could not leave the repository, and the grant then added a search
# and a fetch per external claim on top of it. At 40 a seven-card plan gets 68
# and a fifteen-card one 100. Floor and cap are untouched.
#
# That is three re-tunes read off three separate digs through Actions logs,
# which is why `review_turns_marker` below now puts what a review SPENT on the
# epic's own thread: the fourth re-tune is read, not excavated.
POST_REVIEW_TURNS_BASE = 40        # charter, context, sight, children, thread, result, + the web
POST_REVIEW_TURNS_PER_CARD = 4     # round 1 measured ~2.1/card; round 2 needed more
#: The smallest budget a review gets. Nothing that finished under the previous
#: floor gets less room than it had.
POST_REVIEW_TURNS_FLOOR = 60
#: Above this a bigger number only moves the wall (DRE-2924: the review quality
#: at turn 119 is not the quality at turn 20). The QA critic's retry ceiling.
POST_REVIEW_TURNS_CAP = 140
#: What an UNKNOWN child count gets — the fifteen-card number, never the floor
#: a fifteen-card plan already died at. A Linear read that failed is unknown,
#: not zero (standards/console-honesty.md rule 2). Derived, never a second
#: constant: it is `post_review_turns(15)` and the tests pin the equality.
POST_REVIEW_TURNS_DEFAULT = 100


def post_review_turns(children) -> int:
    """`--max-turns` for the post-approval review of a plan with `children`
    cards. Never raises: the workflow interpolates this into the action's
    arguments, and a bare `--max-turns` is a run that never starts."""
    try:
        n = int(children)
    except (TypeError, ValueError):
        return POST_REVIEW_TURNS_DEFAULT
    if n < 0:
        return POST_REVIEW_TURNS_DEFAULT
    sized = POST_REVIEW_TURNS_BASE + POST_REVIEW_TURNS_PER_CARD * n
    return max(POST_REVIEW_TURNS_FLOOR, min(POST_REVIEW_TURNS_CAP, sized))


# Handed to BOTH critics (DRE-3243) and to neither's substitute: the one-off
# stage reads a single card that has no children, so there is no state block to
# give it. Interpolated rather than repeated, so the two readings of a lane
# cannot drift into two answers.
_CHILD_STATE_BLOCK = """\
READ EACH CHILD'S STATE, NOT ONLY ITS TEXT. Every child record carries a
`state` — the lane the card is in right now — and it is what says whether the
card is to-build at all.

  - **Done, Canceled or Duplicate: delivered or dropped, never to-build.** A
    Done card DESCRIBES the work it delivered; that is the normal shape of
    every Done card, not a card asking an agent to build shipped work. So
    "its deliverable already exists on `main`" is NOT a finding against it,
    and neither is a collision with a sibling over a file it has already
    merged.
  - **In Progress or In Review: a run or a pull request is in flight.** Judge
    it on what it will LAND, not on whether its files are in the tree yet.
  - **Anything else — Backlog, Todo, Triage — is still to build**, and every
    finding you would normally make stands, this one included.

A card's text and the repository cannot tell you which of those it is. Read the
state before you report that work already exists: on DRE-3164 that reading cost
a sound plan its second round and parked it at the bound."""

_PRE_CHARTER = """\
YOU ARE THE FIRST CRITIC. You review a plan that is still a MOVING DOCUMENT,
before the CEO has spent any attention on it.

YOUR QUESTION: {question}

That is the whole of your charter, and it is deliberately narrow. INTENT IS
NOT SETTLED YET, so you cannot usefully ask what is missing from the
specification — there is no specification. A second critic asks that question
after approval, against the frozen text.

WHAT YOU CHECK:
  - Does every card carry observable acceptance criteria — something a reader
    can check happened, not "works well"?
  - Does every card name the repo it builds in, and a rough size?
  - Do the cards SUM to the epic? A surface, an outcome or a component the
    epic asks for and no card carries is a silent omission. For design work
    the mechanical form of this is `plan_critic.py mechanical`, which reuses
    scripts/design_parity.py — run it, do not re-derive it.
  - Is anything plainly ambiguous — a card two competent people would build
    two different ways?
  - Do two cards touch the same file? Siblings that edit one file conflict on
    every merge.

{child_state}

WHAT YOU DO NOT DO: you do not redesign the plan, you do not rank the work,
and you do not judge whether the epic is worth doing. That is the CEO's call
and the plan exists to let them make it.

CROSS-EPIC SCOPE: THIS EPIC ONLY. You are given this epic, its cards and its
artifact, and nothing else. You cannot see other epics in flight and must not
guess at them — the post-approval critic has that sight and that job.
"""

_POST_CHARTER = """\
YOU ARE THE SECOND CRITIC. The CEO has APPROVED this plan. The text you are
reading is no longer a proposal — it is now the SPECIFICATION that agents will
build from, unchanged, starting as soon as you finish.

YOUR QUESTION: {question}

This is the last point at which a gap is free to fix. After you, the cards
enter Backlog and agents build them.

WHAT YOU CHECK:
  - What will an agent get wrong? Read each card as the only instruction its
    agent will ever receive, with no author to ask.
  - Does a card reference something that does not exist yet — a table, a
    module, a route, an env var, a sibling's output — without a card that
    creates it first, and an ordering that says so?
  - Has every database and infrastructure card got the operator step it
    manufactures? Agents have no cloud credentials; a migration or a deploy
    with no operator step is work that cannot land.
  - Is any external claim in the plan actually true? Check vendor behaviour
    rather than accepting an assertion about it. The first version of the
    Wave 1.5 plan reached main carrying three false claims.
  - COLLISIONS. Two epics in flight that would edit the same interface, schema
    field, route or file. You are the cheapest place to catch one: you are
    already reading a full plan with fresh eyes, and a planner working inside
    one epic cannot see the other.

{child_state}

SENDING IT BACK SHOULD BE RARE. How often you send a plan back is the honest
measure of how good the first critic is — so when you do, the reason has to be
specific enough that the first critic could have caught it.

WHEN YOU FIND COLLISIONS, write `collisions: <n>` on its own line in your
result file, naming each one in your reason. That count is recorded separately
from collisions found later, and the gap between the two is the tripwire for
whether this check needs its own pass.
{prior}{sight}"""

_ONE_OFF_CHARTER = """\
YOU ARE THE PRE-APPROVAL CRITIC, and the card in front of you is not a plan.
It has been classified as ONE-OFF — one card, one pull request — so it owes no
plan document and no green light, and NOBODY ELSE WILL READ IT. The moment you
pass it, it goes to the build queue and is built unattended.

YOUR QUESTION: {question}

You are the LAST reader before the money is spent. There is no CEO reading this
one, and the code critic on the pull request is downstream of a build that has
already been paid for.

WHAT YOU CHECK:
  - IS ANY OF IT A DECISION? A card that asks whether we should do something —
    a price, a policy, what to make public, which of two defensible options to
    take — is not work, however small it looks. Nobody can build an answer to
    it, and an agent asked to will invent one. This is the case this check
    exists for, and it is a SEND_BACK.
  - Is it really ONE pull request? Contracts between pieces, two languages or
    tiers, a criterion counting something the card never enumerates, an
    unbounded "every surface" — any one of those is an epic wearing a one-off's
    stamp (standards/card-quality.md).
  - Can an agent tell when it is DONE, from this card alone, with no author to
    ask? An exit condition that needs a person to drive a flow or observe live
    state is not something an unattended run can satisfy.
  - Does the card need something that does not exist yet, with nothing to
    create it first?

READ THE SHAPE STAMP'S OWN REASON. The classifier wrote one sentence saying why
it called this a one-off. You are checking that sentence as much as the card:
where it and the card disagree, the card is what is true.

WHAT YOU DO NOT DO: you do not rewrite the card, you do not size the work, and
you do not judge whether it is worth doing. You answer one question, and you
send back only what an unattended agent genuinely cannot build.

A SEND_BACK IS NOT A REJECTION OF THE WORK. It routes the card to the person
who can settle the thing you found, so state that thing in one plain-English
line — no file paths, no code — because a non-technical reader is who answers
it.

SCOPE: THIS CARD ONLY. You are given the card, its shape stamp and the
repository. You cannot see other work in flight and must not guess at it.
"""

STAGES: dict[str, dict] = {
    STAGE_PRE: {
        "agent": AGENT_PRE,
        "title": "First critic — before the CEO reads it",
        "question": "Is this fit to take the CEO's time?",
        "template": _PRE_CHARTER,
    },
    STAGE_POST: {
        "agent": AGENT_POST,
        "title": "Second critic — after the CEO approves it",
        "question": "Given this is now the specification, what is missing?",
        "template": _POST_CHARTER,
    },
    STAGE_ONE_OFF: {
        # The FIRST critic's agent, deliberately. One reader, two routes.
        "agent": AGENT_PRE,
        "title": "Pre-approval critic — before this is built",
        "question": (
            "Is this one pull request of work an agent can build unattended, "
            "with nothing in it that is a decision?"
        ),
        "template": _ONE_OFF_CHARTER,
    },
}


def question(stage: str) -> str:
    """The one question this stage asks. KeyError on an unknown stage — a typo
    must fail loudly rather than silently produce a critic with no charter."""
    return STAGES[stage]["question"]


def agent(stage: str) -> str:
    """The roster/ladder name this stage runs as.

    Read rather than assumed, because two stages share one: the one-off stage
    IS the first critic, so `model_fallback.py select` and
    `assemble_context.py assemble` are handed the same role on both routes and
    no third entry exists to drift.
    """
    return STAGES[stage]["agent"]


def charter(stage: str, sight: str = "", prior: str = "") -> str:
    """The stage's prompt block, as the workflow interpolates it.

    `sight` is the cross-epic scope block and reaches the POST stage only: the
    other two charters state they have no cross-epic sight, and handing one to
    them would be the same critic twice.

    `prior` is the previous round's findings block (`prior_round_block`,
    DRE-4115) and reaches the POST stage only, for the same reason the bound
    it feeds is the post stage's: a re-plan sits between two post rounds, and
    the round after it is asked which of the earlier findings the revision
    left open. Empty on a first round, and the charter then says nothing
    about it.

    `child_state` is passed to every stage and referenced by the two that read
    CHILDREN. `str.format` ignores a keyword no template names, so the one-off
    charter — one card, no children — is unchanged by it.
    """
    spec = STAGES[stage]
    if stage != STAGE_POST:
        return spec["template"].format(question=spec["question"],
                                       child_state=_CHILD_STATE_BLOCK)
    return spec["template"].format(
        question=spec["question"],
        child_state=_CHILD_STATE_BLOCK,
        prior=("\n" + prior.rstrip("\n") + "\n") if prior.strip() else "",
        sight=("\n" + sight.rstrip("\n") + "\n") if sight.strip() else "",
    )


# --- The result file --------------------------------------------------------

# `PLAN-CRITIC: SEND_BACK — reason` / `PLAN-CRITIC: PASS`. The dash separator
# accepts the em-dash the comms standard prefers and the ASCII forms an agent
# may reach for.
_RESULT_LINE = re.compile(
    rf"^{re.escape(RESULT_PREFIX)}\s*(?P<result>[A-Z_]+)"
    r"(?:\s*(?:—|–|--?|:)\s*(?P<reason>\S.*))?\s*$"
)

_COLLISIONS_LINE = re.compile(r"^\s*collisions:\s*(\d+)\s*$", re.MULTILINE)


def one_line(text: str, limit: int = 300) -> str:
    """A reason, flattened to something that cannot forge a step output.

    `$GITHUB_OUTPUT` is line-oriented and this text is written by an agent that
    has just read attacker-writable epic prose. A newline in it would write a
    step output of the workflow's own — `action=proceed` among them — which is
    the same class of hole `sanitize_untrusted.py` closes for card titles.
    """
    flat = " ".join(str(text).split())
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


def read_result(text: str) -> tuple[str, str]:
    """`(result, reason)` from a critic's result file.

    The FIRST result line wins: whatever the critic writes underneath is its
    working, and a second header further down must not be able to overturn the
    decision it already recorded.

    A SEND_BACK with no reason reads as NO_RESULT. The card requires the reason
    to be attached, and a reason-less send-back is a stall dressed up.

    A PASS may carry a reason and keeps it (DRE-3041). On the one-off route the
    critic's own sentence is what lands on the card — "the reason is posted on
    the card so the scorer can grade critic against classifier against outcome"
    — and a pass whose reason is discarded leaves the card saying only that
    something passed. A pass with no reason is still a pass; nothing downstream
    reads the field to decide anything.
    """
    for raw in (text or "").splitlines():
        m = _RESULT_LINE.match(raw.strip())
        if not m:
            continue
        result = m.group("result")
        reason = one_line(m.group("reason") or "")
        if result == PASS:
            return PASS, reason
        if result == SEND_BACK and reason:
            return SEND_BACK, reason
        return NO_RESULT, reason
    return NO_RESULT, ""


def result_line(result: str, reason: str = "") -> str:
    """The header a critic writes as the first line of its result file."""
    reason = one_line(reason)
    return f"{RESULT_PREFIX} {result}" + (f" — {reason}" if reason else "")


def collisions_declared(text: str) -> int:
    """The `collisions: <n>` count from a post critic's result file, or 0."""
    hits = _COLLISIONS_LINE.findall(text or "")
    return int(hits[-1]) if hits else 0


# --- Every finding, in the round it sees them (DRE-3251) --------------------
#
# The first line above is the WORST gap and it is all the marker and the bound
# ever read. What was missing was everywhere else: the result grammar asked for
# one line, the standard asked for one line, and the re-plan step was told to
# fix "exactly that" — so a critic that had found four defects reported them
# one per round.
#
# THE MEASUREMENT: DRE-3164, 2026-09-06 PT. The post-approval critic sent the
# plan back four times in a row, each round carrying ONE finding, each real,
# each different, and round 4's finding was already present in the plan round 1
# read. Four rounds, four re-plans, three parks with `needs-human`, four CEO
# approvals, ~40 minutes of the CEO's attention — for findings that could all
# have been made, and fixed, in one pass. The critic READ the whole plan every
# round; it only REPORTED one of it.
#
# So the result file grows a BODY and nothing else moves. The list rides in the
# 🛑 note beside the record, never in it: `parse_markers`, `trusted_bodies` and
# the two-round bound are this gate's credential and a card about reporting
# must not touch them.

#: The heading the note lists a round's findings under. Also what tells a test
#: whether a note carries a list at all.
FINDINGS_HEADING = "Every finding this round"

#: ...and the heading for a list that is NOT one round's: the whole history the
#: one-off rewrite park hands back (DRE-4058). Its own words because the claim
#: is different — these were raised across rounds the CEO has already answered,
#: and calling them "this round" would read as the critic inventing five new
#: findings in one pass.
FINDINGS_SO_FAR_HEADING = "Every finding raised on this card so far"

#: The sentence every critic prompt spells this grammar with, in one place so
#: the four prompts on the rail cannot drift apart from each other — the
#: one-off stage shares the first critic's result grammar, so it shares this.
NAME_EVERYTHING_NOW = "NAME EVERY FINDING YOU HAVE, IN THE ROUND YOU SEE IT."

#: How many FURTHER findings one round may report. A bound rather than a
#: judgement: the list reaches a step output and a prompt, and a runaway body
#: must cost a truncated list, never a step that cannot be written.
MAX_FINDINGS = 20

# A further finding is a numbered line at COLUMN ZERO. Indentation is the whole
# of the distinction: a nested markdown list inside the critic's working is
# elaboration of a finding it already named, and reading those as findings of
# their own would hand the re-plan the same gap three times.
_FURTHER_FINDING = re.compile(r"^(?P<n>\d+)[.)]\s+(?P<text>\S.*?)\s*$")


def further_findings(text: str) -> list[str]:
    """The numbered body of a critic's result file, in the order it wrote them.

    Read from the WHOLE body, not from the block that happens to follow the
    header: over-reporting costs the re-plan a line it has already fixed, and
    under-reporting costs a round, a park and a CEO approval. Each item is
    flattened through `one_line` for the same reason the reason field is — it
    travels to `$GITHUB_OUTPUT` and into a prompt.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        m = _FURTHER_FINDING.match(raw)
        if not m:
            continue
        item = one_line(m.group("text"))
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= MAX_FINDINGS:
            break
    return out


def all_findings(text: str) -> list[str]:
    """Every finding of one round, ranked, worst first.

    The first line is the worst gap — the one the marker carries and the one
    the CEO reads in the headline — and the numbered list under it is the rest.
    A PASS and a crash have no findings: only a send-back is a round that found
    something, and `read_result` is the one place that decides which is which.
    """
    result, reason = read_result(text)
    if result != SEND_BACK or not reason:
        return []
    return [reason] + [f for f in further_findings(text) if f != reason]


def findings_block(items: list[str]) -> str:
    """The ranked findings as a numbered list, one per line — the form that
    reaches the re-plan's prompt and the note."""
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items or [], 1))


def findings_section(items: list[str]) -> str:
    """The note's list block, or "" when the first line already said it all.

    A one-item list under a headline that carries the same sentence is noise on
    the epic the CEO reads, so a single-finding send-back keeps today's shape.
    """
    if len(items or []) < 2:
        return ""
    return "\n\n".join([
        f"{FINDINGS_HEADING} ({len(items)}), ranked — the revision answers all "
        "of them, and the next round checks those fixes rather than finding "
        "these again:",
        findings_block(items),
    ])


def findings_so_far_section(items: list[str]) -> str:
    """The list the one-off rewrite park carries: every finding this CARD has
    collected, oldest first (DRE-4058).

    Printed even for a single item, unlike the round's own list above: there the
    headline already said the one finding, here the headline asks for a rewrite
    and a rewrite request that names nothing is the ask without the work.
    """
    if not items:
        return ""
    return "\n\n".join([
        f"{FINDINGS_SO_FAR_HEADING} ({len(items)}), oldest first — one rewrite "
        "answers all of them, rather than one answer each:",
        findings_block(items),
    ])


# --- The record -------------------------------------------------------------

# One marker per round, on the epic. This is where the send-back RATE is read
# from — the same convention as the design-parity ledger and the
# `model-attempt:` heartbeat: a durable, timestamped line in the thread, not a
# number somebody remembers.
_MARKER = re.compile(
    # `[\w-]`, not `\w`: the one-off stage's name carries a hyphen, and a marker
    # the reader cannot parse is a round the record does not hold (DRE-3041).
    rf"^{re.escape(MARKER_PREFIX)}\s+stage=(?P<stage>[\w-]+)\s+round=(?P<round>\d+)\s+"
    r"result=(?P<result>[A-Z_]+)\s+collisions=(?P<collisions>\d+)"
    # `open=<n>` — how many of the PREVIOUS round's findings this round found
    # still open (DRE-4115). Optional: every marker written before it existed,
    # and every pre-stage one, has no reading, and `parse_markers` says None.
    r"(?:\s+open=(?P<open>\d+))?"
    r"(?:\s+—\s+(?P<reason>.*))?$",
    re.MULTILINE,
)

_CYCLE = re.compile(
    rf"^{re.escape(CYCLE_PREFIX)}\s+start\s+epic=(?P<epic>\S+)\s*$",
    re.MULTILINE,
)

_LATE_COLLISION = re.compile(
    rf"^{re.escape(LATE_COLLISION_PREFIX)}\s+epic=(?P<epic>\S+)\s+with=(?P<with>\S+)"
    r"(?:\s+—\s+(?P<detail>.*))?$",
    re.MULTILINE,
)


def marker(stage: str, round_n: int, result: str, reason: str = "",
           collisions: int = 0, open_count: int | None = None) -> str:
    """The machine-parseable record of one critic round.

    `open_count` is how many of the previous round's findings this round found
    still open (DRE-4115) — written only when the decision had a reading, so
    the sweep's gate (`post_release`) can ask the same question `decide`
    answered off the same record, and a marker with no field is honestly
    UNKNOWN rather than silently "answered".
    """
    line = (f"{MARKER_PREFIX} stage={stage} round={int(round_n)} "
            f"result={result} collisions={int(collisions)}")
    if open_count is not None:
        line += f" open={int(open_count)}"
    reason = one_line(reason)
    return line + (f" — {reason}" if reason else "")


def late_collision_marker(epic: str, other: str, detail: str) -> str:
    """The record of a collision found AFTER the cards reached Backlog.

    Posted by whoever finds one (`plan_critic.py late-collision`), on the epic
    the collision belongs to. It is the tripwire half of the D3 measurement:
    the post critic's own count says how many it caught, this says how many it
    did not, and only the pair of them makes "the check has to split back out"
    a measurement rather than a memory.
    """
    return (f"{LATE_COLLISION_PREFIX} epic={epic} with={other}"
            f" — {one_line(detail)}")


# --- The tombstone (DRE-3241) -----------------------------------------------
#
# A review that DIES — the action ended without the decision step ever
# running — used to leave nothing. On 2026-09-05 round 2 of the second critic
# on DRE-3164 died at its turn ceiling twice, the job went red, no marker was
# written, and every sweep afterwards read the newest trusted marker — round
# ONE's send-back, a finding the re-plan had already answered — and told the
# CEO to move an epic to the lane it was already in. Nothing on the epic could
# tell "the critic rejected it" from "the critic never finished".
#
# So a death is recorded in the same shape as a round: one line, alone in its
# comment, pipeline-authored — and under a DIFFERENT prefix, because it is not
# a round. It carries no result, spends nothing of the bound, and the sweep
# reads it as "the review died; it was not a rejection".

_DEATH = re.compile(
    rf"^🪦\s+{re.escape(DEATH_PREFIX)}\s+stage=(?P<stage>[\w-]+)\s+run=(?P<run>[\w-]+)"
    r"\s+attempt=(?P<attempt>\d+|\?)\s+step=(?P<step>[\w-]+)"
    r"\s+subtype=(?P<subtype>[\w-]+|\?)\s+turns=(?P<turns>\d+|\?)"
    r"\s+ceiling=(?P<ceiling>\d+|\?)\s*$",
    re.MULTILINE,
)

_TOKEN = re.compile(r"[\w-]+")

#: A MODEL id, which is the same token widened by one character: vendors
#: version with dots (`claude-3.5-…`) and a `?` where a real id belongs would
#: throw away the one fact the receipt exists to carry (DRE-3498).
_MODEL_TOKEN = re.compile(r"[\w.-]+")


def _token(value, pattern=_TOKEN) -> str:
    """One `[\\w-]+` token, or `?`. Every field of the tombstone lands in a
    credential line, and a value the action's own file hands us (the subtype)
    must not be able to carry a second line or a second record into it.

    `pattern` widens the alphabet where a field legitimately needs more — one
    seam rather than a second near-identical function, so there is one place
    where "what may reach a signed line" is decided."""
    m = pattern.fullmatch(str(value).strip()) if value not in (None, "") else None
    return m.group(0) if m else "?"


def _count(value) -> str:
    """A non-negative integer as text, or `?` — unknown is unknown, never 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            return "?"
    return str(int(value)) if value >= 0 else "?"


def death_marker(stage: str, run, attempt, step: str, subtype, turns,
                 ceiling) -> str:
    """The machine-parseable record of a review that died before deciding."""
    return (f"🪦 {DEATH_PREFIX} stage={_token(stage)} run={_token(run)} "
            f"attempt={_count(attempt)} step={_token(step)} "
            f"subtype={_token(subtype)} turns={_count(turns)} "
            f"ceiling={_count(ceiling)}")


def parse_deaths(bodies: list) -> list[dict]:
    """Every tombstone in a thread, oldest→newest — same credential as
    `parse_markers`: pipeline-authored AND alone in its comment."""
    rows = []
    for body in trusted_bodies(bodies):
        m = _sole_record(_DEATH, body)
        if not m:
            continue
        rows.append({
            "stage": m.group("stage"),
            "run": m.group("run"),
            "attempt": None if m.group("attempt") == "?" else int(m.group("attempt")),
            "step": m.group("step"),
            "subtype": None if m.group("subtype") == "?" else m.group("subtype"),
            "turns": None if m.group("turns") == "?" else int(m.group("turns")),
            "ceiling": None if m.group("ceiling") == "?" else int(m.group("ceiling")),
        })
    return rows


# --- What the review SPENT (DRE-3498) ---------------------------------------
#
# The ceiling above has been re-tuned three times, and each re-tune was one
# archaeology dig through Actions logs for one number: DRE-3164's 40-turn wall
# (2026-09-05), the web-tool grant's widening (DRE-2785), and run 34144302622's
# 51 turns at a ceiling of 48 (2026-09-07). Three guesses off three single
# observations, because nothing recorded what a review actually costs.
#
# So every review now says so, on the epic, in the same shape as a round: one
# line, alone in its comment, pipeline-authored. It is a FACT ABOUT A CALL, not
# an act and not a judgement — nothing is refused, recovered or held by it, so
# it carries no act trailer (config/pipeline-acts.json's `unconverted` block
# says why), it spends nothing of the bound, and `parse_markers`/`parse_deaths`
# cannot see it. It is written whether the review passed, sent the plan back or
# DIED — a death at the ceiling is precisely the observation the next re-tune
# needs.
#
# Every field goes through `_token`/`_count`, for the same reason the tombstone
# does: the spend and the model id come off the action's own execution file,
# and a value that could carry a newline could carry a forged round into a
# comment the pipeline signed.
REVIEW_TURNS_PREFIX = "review-turns:"

_REVIEW_TURNS = re.compile(
    rf"^🧮\s+{re.escape(REVIEW_TURNS_PREFIX)}\s+spent=(?P<spent>\d+|\?)"
    r"\s+ceiling=(?P<ceiling>\d+|\?)\s+children=(?P<children>\d+|\?)"
    r"\s+model=(?P<model>[\w.-]+|\?)\s*$",
    re.MULTILINE,
)


def review_turns_marker(spent, ceiling, children, model) -> str:
    """What one post-approval review cost, as one line for its own comment."""
    return (f"🧮 {REVIEW_TURNS_PREFIX} spent={_count(spent)} "
            f"ceiling={_count(ceiling)} children={_count(children)} "
            f"model={_token(model, _MODEL_TOKEN)}")


def parse_review_turns(bodies: list) -> list[dict]:
    """Every turns receipt in a thread, oldest→newest — the same credential as
    `parse_deaths`: pipeline-authored AND alone in its comment."""
    rows = []
    for body in trusted_bodies(bodies):
        m = _sole_record(_REVIEW_TURNS, body)
        if not m:
            continue
        rows.append({
            "spent": None if m.group("spent") == "?" else int(m.group("spent")),
            "ceiling": None if m.group("ceiling") == "?" else int(m.group("ceiling")),
            "children": None if m.group("children") == "?" else int(m.group("children")),
            "model": None if m.group("model") == "?" else m.group("model"),
        })
    return rows


#: The action's own enum for a run cut off at its ceiling. It is one of the
#: two ways a row reads as the turn cap and no longer the only one — see
#: `hit_the_turn_cap`.
TURN_CAP_SUBTYPE = "error_max_turns"

#: The enum an action reports when it FINISHED. It reaches a tombstone at all
#: only because the step was marked failed for some other reason, so it is
#: never printed as what the review died OF: *died (success)* is a sentence
#: about a run that did not die of succeeding (DRE-3501).
FINISHED_SUBTYPE = "success"


def hit_the_turn_cap(row: dict) -> bool:
    """Did this tombstone's run end AT its turn ceiling?

    Two ways, and the second is the one run 34144302622 needed (DRE-3501): the
    action said `error_max_turns`, OR the row's own numbers say the run reached
    the ceiling it was given. That run FINISHED — `subtype: success`, turn 51
    of a 48-turn ceiling — and the enum alone read it as a death that was not
    a turn cap, so `review_rerun.after_death` left it to a medic that refuses
    turn caps and nothing re-ran the review.

    UNKNOWN IS NOT OVER (standards/console-honesty.md rule 2): with either
    number missing there is nothing to compare, and the subtype is all there
    is. One predicate, here, because `_death_sentence` and
    `review_rerun.after_death` both ask it of the same row — a second copy of
    `turns >= ceiling` is how two files come to disagree about one run.
    """
    row = row or {}
    if row.get("subtype") == TURN_CAP_SUBTYPE:
        return True
    turns, ceiling = row.get("turns"), row.get("ceiling")
    return turns is not None and ceiling is not None and turns >= ceiling


def _death_sentence(row: dict) -> str:
    """The death as one predicate — `ran out of turns — 41 of its 40-turn
    ceiling in run 34008698027 (attempt 2), step \\`posta\\`` — with the
    subject left to the caller. The sweep's detail and the note's opening
    share it, so the two never describe the same run differently.

    The TURNS decide, not the enum (`hit_the_turn_cap`), and no row ever
    prints `(success)`.
    """
    turns = row.get("turns")
    ceiling = row.get("ceiling")
    subtype = row.get("subtype")
    attempt = row.get("attempt")
    where = f"run {row.get('run') or '?'}" + (
        f" (attempt {attempt})" if attempt is not None else "")
    spent = f"{turns} turns" if turns is not None else "an unknown number of turns"
    cap = f" of its {ceiling}-turn ceiling" if ceiling is not None else ""
    if hit_the_turn_cap(row):
        # Both numbers known is the whole of the reading, so it is the whole
        # of the sentence: `51 of its 48-turn ceiling`.
        at_cap = f"{turns} of its {ceiling}-turn ceiling" \
            if turns is not None and ceiling is not None else f"{spent}{cap}"
        how = f"ran out of turns — {at_cap}"
    elif subtype and subtype != FINISHED_SUBTYPE:
        how = f"died ({subtype}) after {spent}{cap}"
    else:
        how = f"died after {spent}{cap}"
    return f"{how} in {where}, step `{row.get('step') or '?'}`"


def trusted_bodies(entries) -> list[str]:
    """The comment bodies this module may read a round record out of.

    A thread entry is either a plain STRING — a body the caller already stands
    behind (the tests' fixtures, and a thread a human hands the CLI) — or a
    RECORD from `linear_ops.py dump-comments --with-authors`, which says who
    wrote it. A record counts only when the pipeline itself wrote it.

    Why (DRE-2721 review): the markers below are this gate's credential, and
    they used to be read off every comment on the epic. Two comments carrying a
    forged `plan-critic: ... result=SEND_BACK` line were enough to make the
    second critic's real, current rejection read as "the bound is already
    spent" — promoting the epic's children to build with the finding
    discarded; one carrying a forged `plan-cycle:` boundary was enough to
    refund a budget that had been legitimately spent, so the plan could circle
    for as long as anyone kept posting one. Anyone with comment access on the
    epic can post either, and `standards/plan-critic.md`'s own worked example
    is a literal boundary line — so this is an accident as much as an attack
    (standards/untrusted-content.md: "a manipulated card or comment must not be
    able to steer an agent").
    """
    out = []
    for entry in entries or []:
        if isinstance(entry, dict):
            if entry.get("authored_by_pipeline"):
                out.append(entry.get("body") or "")
        else:
            out.append(entry or "")
    return out


def _sole_record(pattern, body: str):
    """`pattern` matched against a comment that says NOTHING BUT that line.

    The second half of the credential, and the half `trusted_bodies` cannot
    supply (DRE-2721 review round 3). "The pipeline wrote it" is far wider than
    "this module wrote it": the same shared Linear key posts the PLANNER's plan
    write-up to the same epic — freeform LLM prose derived from the epic's own
    untrusted description, and instructed by `briefs/planner.md` to explain
    this very gate. Matched line-by-line, one sentence of that write-up quoting
    or paraphrasing `standards/plan-critic.md`'s worked example counted as a
    round nobody ran: combined with the critic's own current SEND_BACK it
    reached the bound, and a real rejection — a migration card with no operator
    step — was silently waved through to build. The boundary line does the same
    damage in the other direction, refunding a budget that was legitimately
    spent.

    So a record is one line, alone in its comment. Prose can quote a marker,
    explain one, or be steered by injected card text into echoing one, and none
    of it qualifies — there is no line to embed it in. That is why `decide` and
    `cycle-start` each emit their human note and their record as two SEPARATE
    comments (`--note-file` / `--record-file`, `--record`).

    Returns the match, or None.
    """
    text = (body or "").strip()
    # `\s+` inside the record patterns spans newlines, so a wrapped body could
    # otherwise satisfy a "whole body" match across two lines.
    if "\n" in text or "\r" in text:
        return None
    return pattern.fullmatch(text)


def parse_markers(bodies: list) -> list[dict]:
    """Every critic-round marker in a comment thread, oldest→newest.

    A comment records a round only when the pipeline wrote it
    (`trusted_bodies`) AND it is nothing but the marker (`_sole_record`). Both
    halves are load-bearing: the first keeps a bystander's comment out, the
    second keeps the pipeline's OWN prose out — including a critic's reason
    field quoting a marker, which is why at most one round can ever come from
    one comment.
    """
    rows = []
    for body in trusted_bodies(bodies):
        m = _sole_record(_MARKER, body)
        if not m:
            continue
        rows.append({
            "stage": m.group("stage"),
            "round": int(m.group("round")),
            "result": m.group("result"),
            "collisions": int(m.group("collisions")),
            # None, not 0: a marker with no field recorded no reading.
            "open": int(m.group("open")) if m.group("open") is not None else None,
            "reason": (m.group("reason") or "").strip(),
        })
    return rows


def cycle_marker(epic: str) -> str:
    """The line that opens a planning cycle. One per planning attempt."""
    return f"{CYCLE_PREFIX} start epic={epic}"


def cycle_start_note(epic: str) -> str:
    """The HUMAN half of opening a planning attempt, for the CEO reading the
    thread. It carries no boundary line: `cycle_marker` is posted as its own
    comment right after this one, because a record that shares a comment with
    prose is a record any prose can forge (`_sole_record`).
    """
    return (
        f"📋 A fresh planning attempt on {epic} starts here. Both critics count "
        "their rounds from this point, so a re-planned epic gets its own "
        "revision round rather than inheriting a budget the last attempt "
        "already spent."
    )


def current_cycle(bodies: list, epic: str | None = None) -> list[str]:
    """The comment bodies that belong to the CURRENT planning attempt.

    Everything before the last boundary belongs to a plan that no longer
    exists. A thread with no boundary at all is one cycle — epics planned
    before this existed keep counting exactly the way they did.

    Three things a boundary has to be, because it hands a stage a fresh budget
    and an unbounded loop is how 17 cards sat in a lane for 27 days:

      * ITS OWN COMMENT, and the whole of it (`_sole_record`). A boundary
        quoted inside prose — a critic's reason field, the planner's plan
        write-up, anything an agent wrote after reading untrusted epic text —
        opens nothing.
      * The pipeline's. Only the run that decides an epic is being planned
        writes one, so only its own comments are read (`_entry_trusted`, the
        same predicate `trusted_bodies` applies).
      * About THIS epic, when the caller says which one. The standard's worked
        example names a real epic verbatim, so a boundary is scoped to the epic
        being decided rather than to any epic named anywhere in the thread.
        `epic=None` keeps the old behaviour for callers with no epic in hand
        (the metrics CLIs, and every fixture that names one epic only).

    An earlier version of this docstring claimed a forged boundary could not
    make a plan circle "because the rail runs at most MAX_ROUNDS rounds per
    run". That was only ever true of the PRE stage, where plan.yml hardcodes
    two rounds per job run. The POST stage runs ONE round per run and its bound
    lives entirely in these persisted markers — which is exactly what a forged
    boundary defeated.
    """
    return [_entry_body(entry) for entry in current_cycle_entries(bodies, epic)]


def current_cycle_entries(entries, epic: str | None = None) -> list:
    """`current_cycle`, keeping each comment's RECORD instead of just its text.

    Same credential, same boundary, same scope — this is the one reader that
    needs more of a comment than what it says. `prior_round` reads the round's
    `created_at` off the record so the charter can tell the next critic when
    that round ran, and a reader that flattens the thread to bodies before
    scoping it drops the stamp on the way past. That is exactly what shipped:
    `_cmd_prior_round` scoped through the bodies reader, so `ran_at` was always
    None and the staleness sentence was never emitted in production
    (found in review, DRE-4115).

    A caller that only wants the text keeps calling `current_cycle`, which is
    now this function plus `_entry_body` — one boundary reader, so the two can
    never disagree about which attempt a comment belongs to.
    """
    kept = [entry for entry in (entries or []) if _entry_trusted(entry)]
    start = 0
    for i, entry in enumerate(kept):
        # A body that is exactly a boundary cannot also be exactly a marker, so
        # the old "a marker never opens a cycle" guard is now structural.
        m = _sole_record(_CYCLE, _entry_body(entry))
        if not m:
            continue
        if epic and m.group("epic") != epic:
            continue
        start = i + 1
    return kept[start:]


def send_backs(bodies: list, stage: str) -> int:
    """Failed rounds recorded for this stage, in the bodies you hand it.

    The two stages count separately — `two failed rounds at EITHER critic` —
    so a pre-stage send-back never spends the post stage's budget. The SCOPE is
    the caller's: `decide` is fed `current_cycle(thread)`, because the bound
    belongs to one planning attempt, while `rate` over the whole thread stays
    the epic's lifetime measurement.
    """
    return sum(1 for r in parse_markers(bodies)
               if r["stage"] == stage and r["result"] == SEND_BACK)


def send_back_findings(bodies: list, stage: str) -> list[str]:
    """What each failed round GAVE as its reason, oldest→newest.

    The same rows `send_backs` counts, read for their text instead of their
    number — one parser, so a count and a list can never disagree about which
    rounds happened. Each marker carries the worst finding of its round and only
    that one (`marker`), so this is the spine of the history rather than the
    whole of it; the rest of a round's findings live in the note beside it,
    which is prose and records nothing.

    Why it exists (DRE-4058): when the one-off route reaches its bound the card
    needs REWRITING, and a rewrite can only answer findings somebody names. The
    CEO answered DRE-3879 five times without ever being shown the other four
    findings in one place. A reason-less send-back contributes nothing — there
    is no text to name — which matches `read_result` reading one as NO_RESULT.
    """
    return [r["reason"] for r in parse_markers(bodies)
            if r["stage"] == stage and r["result"] == SEND_BACK and r["reason"]]


# --- The answered round (DRE-4115) ------------------------------------------
#
# After approval every send-back is followed by a re-plan (plan.yml: "Re-plan
# after the second critic sent it back", on every hold), so the round after it
# is reading a DIFFERENT plan. `send_backs` counts markers, and a marker does
# not know whether the plan it argued about still exists. On DRE-4025 round 1's
# four findings were answered in four minutes; round 2, a day and a half later,
# found five things that had changed in the estate meanwhile, and the count —
# "round 2 of 2" beside a footer reading "1/1 rounds" — parked the epic. On
# DRE-3778 the same arithmetic parked five approvals in a row at "round 6 of 2".
#
# So the round after a re-plan is SHOWN what the previous round found, and says
# which of those the revision left open. That line is what the bound reads: a
# finding still open after a repair is "sent back twice and still not fixed";
# a round whose findings were all answered is round 1 of the revision's own
# life. And because the critic's reading is the input, the critic is told the
# previous round's clock too — the plan is older than the estate it is being
# read against, and a change that landed since is a note, not a strike.

#: The line the critic writes: `still-open: none`, or `still-open: 1, 3` — the
#: numbers of the previous round's findings, as the charter numbered them.
STILL_OPEN_PREFIX = "still-open:"
STILL_OPEN_NONE = "none"

_STILL_OPEN_LINE = re.compile(
    rf"^\s*{re.escape(STILL_OPEN_PREFIX)}\s*(?P<items>.*?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _entry_body(entry) -> str:
    return (entry.get("body") or "") if isinstance(entry, dict) else (entry or "")


def _entry_trusted(entry) -> bool:
    return bool(entry.get("authored_by_pipeline")) if isinstance(entry, dict) else True


def prior_round(entries, stage: str) -> dict | None:
    """The LAST send-back round at `stage` in the bodies you hand it — its
    findings, worst first, and the clock its record was posted at.

    Read through the same credential as every other reader (`trusted_bodies`
    + `_sole_record`): the round is a marker the pipeline wrote alone in its
    comment, and its findings are that marker's reason — the worst gap, the
    only one the record carries — plus the numbered list in the NOTE the run
    posted immediately before it (`_cmd_decide` posts the note, then the
    record, as two consecutive comments). The note is prose and records
    nothing; here it is read only for the list under the round's own marker,
    and only when the pipeline wrote it.

    `None` when the stage has no send-back in these bodies — a first round
    is shown nothing.
    """
    previous = None
    found = None
    for entry in entries or []:
        if not _entry_trusted(entry):
            previous = None
            continue
        body = _entry_body(entry)
        m = _sole_record(_MARKER, body)
        if m and m.group("stage") == stage and m.group("result") == SEND_BACK:
            reason = one_line(m.group("reason") or "")
            listed = (further_findings(previous)
                      if previous and FINDINGS_HEADING in previous else [])
            items = ([reason] if reason else []) + [f for f in listed if f != reason]
            found = {
                "findings": items,
                "ran_at": (entry.get("created_at") if isinstance(entry, dict)
                           else None) or None,
            }
        previous = body
    return found


def prior_findings(entries, stage: str) -> list[str]:
    """Every finding the previous send-back round at `stage` reported, worst
    first — what the critic is shown, numbered, and what `still-open:`'s
    numbers index."""
    found = prior_round(entries, stage)
    return list(found["findings"]) if found else []


def _pt_clock(iso: str | None) -> str | None:
    """`2026-09-15 10:43 PT` from a Linear timestamp, or None. Pacific because
    a person reads the charter's sentence about it, and every clock a person
    reads is Pacific (CLAUDE.md); UTC stays inside the record."""
    if not iso:
        return None
    try:
        when = _ts(iso)
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo
        local = when.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # noqa: BLE001 — no tz database: say so rather than lie
        return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return local.strftime("%Y-%m-%d %H:%M PT")


def prior_round_block(findings: list[str], ran_at: str | None = None) -> str:
    """The charter block for a round that follows a re-plan: the previous
    round's findings, numbered, and the instruction to say which still stand.
    Empty when there is no previous round, so a first round's charter says
    nothing about one."""
    if not findings:
        return ""
    clock = _pt_clock(ran_at)
    when = (f"That round ran at {clock}; the estate you are reading the plan "
            "against may have moved since, and a change that landed AFTER the "
            "plan was written is a note for the planner, not a strike against "
            "it.\n" if clock else "")
    return (
        "THE PREVIOUS ROUND OF THIS REVIEW SENT THE PLAN BACK, and the plan has "
        "been revised since to answer it. What that round found, ranked:\n"
        f"{findings_block(findings)}\n"
        f"{when}"
        "For EACH of those, decide whether the plan in front of you now answers "
        "it — then write, on its own line in your result file, exactly one of:\n"
        f"  {STILL_OPEN_PREFIX} {STILL_OPEN_NONE}        the revision answered "
        "every one of them\n"
        f"  {STILL_OPEN_PREFIX} 1, 3        the numbers above that still stand\n"
        "A finding the revision answered is NOT a finding this round — do not "
        "raise it again in other words. A gap that is NEW, in the revision or "
        "in the estate, is a finding, and you name it as one. The bound reads "
        "this line: a plan sent back again with a finding still open parks for "
        "a person; a plan whose revision settled everything is judged on what "
        "you find now."
    )


def still_open_declared(text: str) -> list | None:
    """What the critic wrote after `still-open:` — a list of 1-based numbers,
    `[]` for `none`, `["all"]` for the word, or None when it wrote no line.
    The LAST such line wins, the way `collisions:` is read."""
    hits = _STILL_OPEN_LINE.findall(text or "")
    if not hits:
        return None
    raw = hits[-1].strip().lower().rstrip(".")
    if not raw or raw == STILL_OPEN_NONE:
        return []
    if raw == "all":
        return ["all"]
    return [int(n) for n in re.findall(r"\d+", raw)]


def still_open_findings(text: str, prior: list[str], this_round: list[str]) -> list[str]:
    """Which of the PREVIOUS round's findings this round says still stand.

    The critic's own line when it wrote one; a number naming no prior finding
    is ignored. Without the line, the one reading the text supports on its own
    is repetition: a prior finding raised again VERBATIM is still open, and
    anything else reads as answered — the direction that buys the revision its
    round, bounded by `post_bound_spent`'s cap so a critic that never writes
    the line still cannot circle a plan forever.
    """
    prior = list(prior or [])
    declared = still_open_declared(text)
    if declared is not None:
        if declared == ["all"]:
            return prior
        out: list[str] = []
        for n in declared:
            if 1 <= n <= len(prior) and prior[n - 1] not in out:
                out.append(prior[n - 1])
        return out
    repeated = {one_line(f) for f in (this_round or [])}
    return [f for f in prior if f in repeated]


def rate(bodies: list, stage: str) -> dict:
    """`{rounds, send_backs, rate}` for a stage.

    With no rounds the rate is None, not 0.0 (console-honesty rule 2): "this
    critic has never sent anything back" and "this critic has never run" are
    different facts, and 0.0 renders them identically.
    """
    rows = [r for r in parse_markers(bodies) if r["stage"] == stage]
    rounds = len(rows)
    backs = sum(1 for r in rows if r["result"] == SEND_BACK)
    return {
        "rounds": rounds,
        "send_backs": backs,
        "rate": (backs / rounds) if rounds else None,
    }


def collision_counts(bodies: list) -> dict:
    """The two counters, kept apart: caught by the post critic, and found later."""
    caught = sum(r["collisions"] for r in parse_markers(bodies)
                 if r["stage"] == STAGE_POST)
    later = sum(1 for body in trusted_bodies(bodies)
                if _sole_record(_LATE_COLLISION, body))
    return {"caught_at_review": caught, "found_later": later}


# --- The release the sweep reads (DRE-3059) ---------------------------------
#
# DRE-2721's design is *two critics: one before you read it, one after you
# approve it — and only then are the children promotable*. The second half of
# that sentence had no reader. `reconcile.promote_ready()` released a child
# once its parent epic was active, its blockers were Done and the WIP cap had
# room; it never asked whether the plan had been READ since it was approved.
# On 2026-09-03 the sweep promoted two children eighty-two seconds after the
# CEO approved their epic, with no post-critic verdict on it, because none had
# run (DRE-3058 is why none ran).
#
# So the release becomes a fact the sweep can read, and it is read out of the
# markers `decide` already writes — never out of elapsed time or an adjacent
# lane (standards/console-honesty.md rule 1).
#
# THE SCOPE IS THE PLANNING ATTEMPT, not "newer than the epic's last move into
# an active lane". The route posts the round record and THEN moves the epic to
# In Progress, so the epic's most recent active-lane entry is always NEWER than
# the marker that released it — gating on that timestamp would refuse every
# child of every epic, forever. `current_cycle` is the module's existing answer
# to "does this record belong to the plan we are looking at", it is already
# forgery-resistant, and a re-planned epic gets a fresh boundary — which is
# exactly the invalidation the timestamp was reaching for.

#: The sweep may promote — the second critic has released this plan.
POST_RELEASED = "released"
#: No post-critic round on this planning attempt at all. The incident.
POST_NOT_RUN = "not-run"
#: The critic ran and declined to release the plan — one send-back with the
#: bound unspent, or two with it spent and the epic parked (DRE-3088).
POST_HELD = "held"
#: The newest post-stage record on this attempt is a tombstone: the review
#: DIED before it decided (DRE-3241). Not a rejection, not a round, and not a
#: release either — nothing has read the plan, so the children wait for the
#: review to run again. Distinct from `NO_RESULT`, which is a review that RAN
#: TO ITS DECISION and wrote nothing usable: that one proceeds inside the same
#: run with a ⚠️ note the CEO can see; this one left a red job and no decision.
POST_DIED = "died"

#: The lane a CEO moves an epic to in order to APPROVE its plan. Approval is
#: the In Progress entry (the relay dispatches the activation on it; an epic
#: in Todo dispatches nothing — DRE-2725), and the contract's Green Light exit
#: clause is written in the same terms. `tests/test_plan_critic_wiring.py`
#: pins it to a live lane in config/lane-contract.json.
APPROVAL_LANE = "In Progress"


def reapprove_how() -> str:
    """HOW to ask for the review again, in every notice that asks for it.

    The relay has TWO triggers and this names both (DRE-3292):

      * a transition INTO In Progress, which is how a plan is approved
        (`_is_epic_activation`). DRE-3241's edge is that an epic ALREADY
        sitting there — which is where a dead or unread review leaves it —
        cannot make that move, so this trigger reaches only an epic parked in
        Green Light.
      * a comment whose whole body is `review_rerun.RERUN_REVIEW_ACT`
        (DRE-3287). That one asks for the review directly, from whichever lane
        the epic is in, and it is what the console's Approve posts for an epic
        already In Progress.

    Until the relay learned the act, the only way to ask was to move the epic
    out to Green Light and approve it back in — a two-lane dance asked of a
    person for a review nothing else would start. Now the act is the ask, and
    the approval is the second half for the epic that is parked.

    One sentence, used by every refusal here and quoted verbatim by plan.yml's
    own notices (the wiring test pins the two copies to each other), so no
    receipt can point at a move that does nothing.

    Because the act is embedded in prose, no notice built from this sentence
    can BE the act: the relay matches the whole comment body (`is_rerun_act`),
    so a notice that quoted it alone would re-run the review every time the
    pipeline posted it (DRE-3286).

    A function, not a constant, for one reason: the act belongs to
    `review_rerun`, which imports THIS module. A top-level import here would
    read a half-built module whenever `review_rerun` is the one imported
    first, and the sentence would lose the act silently. Deferred, both orders
    work — the same shape `linear_ops.cmd_epics_in_flight` uses to read
    `IN_FLIGHT_EPIC_STATES` from here.
    """
    import review_rerun

    return (
        f"post a comment on the epic that says exactly "
        f"{review_rerun.RERUN_REVIEW_ACT} — the console's Approve does this "
        f"for an epic already {APPROVAL_LANE} — or, for an epic sitting in "
        f"Green Light, approve it (the console's Approve, or a move to "
        f"{APPROVAL_LANE})"
    )


def __getattr__(name: str):
    """`plan_critic.REAPPROVE_HOW` — the constant every caller has always
    read (PEP 562), built on first read so `reapprove_how`'s deferred import
    can happen. Anything else is the AttributeError it would have been.

    `card_tells` rides the same seam for the same reason (DRE-3079): it IS
    `split_ledger.tells` — the ledger's own reader for DRE-2893's four tells,
    bound rather than re-spelled — and `split_ledger` imports this module
    through `planner_score`, so it cannot be imported at module scope here.
    """
    if name == "REAPPROVE_HOW":
        return reapprove_how()
    if name == "card_tells":
        return _split_ledger().tells
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


#: What happens after a review DIES, in every notice about one (DRE-3289).
#:
#: Deliberately NOT `REAPPROVE_HOW`: a dead review used to end with an ask made
#: of a CEO who has nothing to decide about it — the plan was never read, so
#: there is no judgement to make — and the epic sat In Progress with nothing
#: scheduled until a person made the two-step move by hand. The workflow now
#: asks for the run itself, once, with the headroom `review_rerun.retry_ceiling`
#: sizes, and only a SECOND death puts it in front of a person. One sentence,
#: used by the note the run posts and by the sweep's own refusal, so the two
#: never describe one dead run differently.
#:
#: True of every death, not only a turn cap: `review_rerun.after_death` leaves
#: any other subtype to the medic, which retries it once already
#: (`medic_retry.RULE_TURN_EXHAUSTION` is the one death it refuses). Either way
#: the review is started again by the pipeline, once, and the second death is
#: the one that asks for a person.
DEAD_REVIEW_NEXT = (
    "The review is started again by the pipeline, on its own, once — a run "
    "that ran out of turns gets a higher ceiling, because the same run at the "
    "same ceiling hits the same wall. A second death parks the epic with "
    "needs-human for an operator to read: a plan no review can finish needs a "
    "person, not a third attempt."
)

#: Idempotency tags for the refusals, in the `dead_run.DEAD_TAG` shape the
#: sweep already surfaces refusals under. THREE of them, deliberately: the
#: sweep posts each refusal at most once per tag, and "nobody has read this
#: plan", "the critic found a gap" and "the review died" are different facts
#: with different next actions — one tag would let the first silence the
#: others forever.
POST_UNREAD_TAG = "plan-critic-post-unread"
POST_SENT_BACK_TAG = "plan-critic-post-sent-back"
POST_DIED_TAG = "plan-critic-post-died"

#: Epics green-lit before this instant are NOT re-gated retroactively.
#:
#: Written down rather than computed: an epic approved before this shipped has
#: no post-critic marker on it and never will, so gating it would freeze every
#: child of every epic in flight on the day this merges. The date is the end of
#: the day the change was built, so everything already approved is covered and
#: nothing approved afterwards escapes. `tests/test_plan_critic.py` pins it.
GATED_FROM = "2026-09-05T00:00:00Z"


def post_release(bodies: list, epic: str | None = None) -> tuple[str, str]:
    """Has the second critic released this epic's children? `(state, detail)`.

    `state` is one of POST_RELEASED / POST_NOT_RUN / POST_HELD, and `detail` is
    the critic's own words when it has any.

    The two ways a plan is released, and each of them is one the route
    already takes — the gate and `decide` must agree about the same marker or
    the sweep and the activate route disagree about the same epic:

      * `result=PASS` — the critic passed it.
      * `result=NO_RESULT` — a crash is not a rejection (console-honesty rule
        1). The critic did not decide anything, so it does not get to stop
        anything, and the route proceeds on one too.

    MAX_ROUNDS failed rounds — the bound — does NOT release the children
    (DRE-3088). Two failed rounds at the second critic and the plan parks for
    the CEO with `needs-human` and both findings; building a plan the critic
    held twice is the wrong thing to do with it, and Green Light with the
    hold label is a watched queue, not the unread lane where the 27-day
    failure lived. `decide` holds on the same round, so the two agree.

    Anything else holds. The vocabulary this module writes is SEND_BACK, but an
    unrecognised verdict is still the critic declining to release the plan, and
    reading an unknown result as a pass is the one direction that must never
    happen.

    A TOMBSTONE newer than every round (DRE-3241) is POST_DIED: the review
    died before it decided, so the newest ROUND is stale — it is the round the
    re-plan already answered — and quoting it is how the sweep spent a night
    telling the CEO the wrong thing. A tombstone OLDER than the newest round is
    history: the re-run the tombstone asked for happened, and that round is
    the record. Deaths never count as rounds, so the bound below is unmoved.
    """
    cycle = current_cycle(bodies, epic)
    rows = [r for r in parse_markers(cycle) if r["stage"] == STAGE_POST]
    # Order between the two record kinds is the comment order, so walk the
    # cycle once and remember which kind came last.
    last_kind = None
    last_death = None
    for body in trusted_bodies(cycle):
        m = _sole_record(_MARKER, body)
        if m:
            if m.group("stage") == STAGE_POST:
                last_kind = "round"
            continue
        d = _sole_record(_DEATH, body)
        if d and d.group("stage") == STAGE_POST:
            last_kind = "death"
            last_death = parse_deaths([body])[0]
    if last_kind == "death":
        return POST_DIED, (
            "the review " + _death_sentence(last_death) + ". It was not a "
            "rejection: the critic decided nothing, and this round does not "
            "count toward the bound"
        )
    if not rows:
        return POST_NOT_RUN, ""
    last = rows[-1]
    if last["result"] == PASS:
        return POST_RELEASED, "the second critic passed this plan"
    if last["result"] == NO_RESULT:
        return POST_RELEASED, (
            "the second critic produced no result — a crash is not a rejection"
        )
    failed = [r for r in rows if r["result"] not in (PASS, NO_RESULT)]
    # The bound, read off the same record `decide` wrote (DRE-4115): the
    # newest failed round's `open=` field says how many of the round before
    # it the revision left open. No field is no reading — the arithmetic
    # every marker before the field was parked under.
    prior, open_count = len(failed) - 1, last["open"]
    if post_bound_spent(prior, open_count):
        reasons = "; ".join(r["reason"] or "none given" for r in failed)
        if open_count is None:
            return POST_HELD, (
                f"{_count_word(len(failed))} failed rounds at the second critic — "
                "the bound, so the plan is parked for the CEO with needs-human "
                "rather than built as it stands. The critic's stated reasons, "
                "unresolved: " + reasons
            )
        if open_count > 0:
            return POST_HELD, (
                f"{_count_word(len(failed))} failed rounds at the second critic "
                "and the revision did not settle it — the bound, so the plan is "
                "parked for the CEO with needs-human rather than built as it "
                f"stands. Still open after the revision: {open_count} of the "
                "previous round's findings. The critic's stated reasons: "
                + reasons
            )
        return POST_HELD, (
            f"{_count_word(len(failed))} failed rounds at the second critic — "
            f"the bound. The plan has been revised {_count_word(prior)} times "
            "since it was approved, each time answering everything the critic "
            "named, and the review still finds new gaps, so it is parked for "
            "the CEO with needs-human rather than circling. The critic's "
            "stated reasons: " + reasons
        )
    if open_count == 0 and prior >= 1:
        return POST_HELD, (
            f"{last['reason']} — the revision answered every finding of the "
            "round before, so this is the revised plan's own first finding "
            "and the review re-runs on the next revision"
        )
    return POST_HELD, last["reason"]


def post_bound_reached(bodies: list, epic: str | None = None) -> bool:
    """Is this planning attempt's post-approval budget SPENT — the state the
    workflow parked the epic in with `needs-human`? Read off the record the
    way `post_release` reads it, so the route step and the sweep's gate can
    never disagree about whether an epic is parked."""
    cycle = current_cycle(bodies, epic)
    rows = [r for r in parse_markers(cycle) if r["stage"] == STAGE_POST]
    if not rows or rows[-1]["result"] in (PASS, NO_RESULT):
        return False
    failed = [r for r in rows if r["result"] not in (PASS, NO_RESULT)]
    return post_bound_spent(len(failed) - 1, rows[-1]["open"])


def opens_fresh_attempt(bodies: list, epic: str | None, reason: str | None) -> bool:
    """Should this ACTIVATE-route run open a new planning cycle (DRE-4115)?

    Yes when a PERSON is re-running a review the bound has parked: the park
    asked a person to settle the plan, and clearing `needs-human` then posting
    the act (`reason: re-run`) or approving the epic back out of Green Light
    (no `reason` at all) is that person saying it is settled. The review that
    follows judges the settled plan on its own rounds rather than inheriting
    the ones it has already answered — before this, DRE-3778 was approved five
    times and came back at "round 5 of 2", then 6.

    ONLY for those two human asks — an ALLOWLIST, not a denylist of the
    pipeline's reasons. The pipeline's own asks today are `re-review` (the
    same-cards re-run after a re-plan) and `review-retry` (a dead review's
    retry); a boundary on either would refund the budget on every round, so
    nothing would ever park, and would cut the tombstone the retry ceiling is
    sized from out of the cycle. A dispatcher added later with a reason of
    its own must not inherit a refund nobody decided on, so any reason that
    is not one of the two a person produces keeps the attempt. The
    answered/open reading (`post_bound_spent`) is what judges the pipeline's
    own re-review.

    And never when the bound has NOT been reached: a person re-running round 2
    (DRE-4112's recovery from a killed dispatch) is asking for round 2, and
    round 2 is judged on whether the revision answered round 1. Every reset
    costs a human act at a park, which is what keeps the loop finite.
    """
    import review_rerun  # deferred: it imports this module

    if (reason or "").strip() not in ("", review_rerun.REASON_RERUN_ACT):
        return False
    return post_bound_reached(bodies, epic)


def promotion_refusal(identifier: str, epic: str, green_lit_at: str | None,
                      bodies: list | None, *,
                      gated_from: str = GATED_FROM) -> str | None:
    """Why `identifier` must not promote yet, or None to let it through.

    Two abstentions, both in the direction that keeps the board moving, and
    both because unknown is unknown rather than "no" (console-honesty rule 2):
    an epic whose green light Linear cannot report, and a comment thread the
    sweep could not read (`bodies is None`, which is NOT the same fact as an
    epic with no comments — that one is the incident, and it refuses).
    """
    if bodies is None or not green_lit_at:
        return None
    try:
        if _ts(green_lit_at) < _ts(gated_from):
            return None
    except ValueError:
        return None  # a timestamp Linear gave us and we cannot read is unknown
    state, detail = post_release(bodies, epic)
    if state == POST_RELEASED:
        return None
    when = _ts(green_lit_at).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if state == POST_NOT_RUN:
        return (
            f"🚨 {POST_UNREAD_TAG}: {identifier}'s epic {epic} was approved at "
            f"{when} but the second critic has not passed it — holding.\n\n"
            "Two critics review a plan: one before the CEO reads it, one after "
            "the CEO approves it — and only then are the children promotable. "
            "Nothing has reviewed this plan since it was approved, so nobody "
            "has asked what an agent will get wrong with it as the "
            "specification.\n\n"
            f"**To let it through:** {reapprove_how()}. That re-runs the "
            "post-approval review, and the children promote on the next sweep "
            "once it passes."
        )
    if state == POST_DIED:
        deaths = parse_deaths(current_cycle(bodies, epic))
        ran = (deaths[-1].get("run") if deaths else None) or "?"
        return (
            f"🚨 {POST_DIED_TAG}: {identifier}'s epic {epic} was approved at "
            f"{when} but the post-approval review died before it decided — "
            f"holding: {one_line(detail)}.\n\n"
            "Nothing has been found wrong with the plan and nothing has "
            "started building; the children stay in Backlog until the review "
            "runs again and passes.\n\n"
            f"**Nothing here is yours to decide.** {DEAD_REVIEW_NEXT} This is "
            f"waiting on the re-run of run {ran}."
        )
    quoted = one_line(detail) or "no reason recorded"
    return (
        f"🚨 {POST_SENT_BACK_TAG}: {identifier}'s epic {epic} was approved at "
        f"{when} but the second critic sent the plan back — holding. The "
        f"critic's reason: {quoted}\n\n"
        "The children stay in Backlog until the gap is settled. The plan has "
        "been revised with the critic's finding, and the review is run again "
        "by the pipeline itself — a revision that changed no card is not a "
        "decision anyone is waiting on.\n\n"
        "**If the epic is sitting in Green Light**, the revision added or "
        "removed cards, and that plan is waiting on the CEO to read it. A plan "
        "sent back twice parks with needs-human rather than being built as it "
        "stands."
    )


def refusal_tag(refusal: str | None) -> str | None:
    """The idempotency tag `refusal` is surfaced under, or None if it is not
    one of this module's refusals.

    Read off the notice by the module that wrote it, never inferred by the
    caller — the same contract `routing_verdict.refusal_tag` has, and for the
    same reason: pair a notice with the wrong tag and two refusals silence
    each other.
    """
    first = ((refusal or "").splitlines() or [""])[0]
    for tag in (POST_UNREAD_TAG, POST_SENT_BACK_TAG, POST_DIED_TAG):
        if first.startswith(f"🚨 {tag}:"):
            return tag
    return None


def death_note(epic: str, row: dict) -> str:
    """The HUMAN half of a dead review, for the CEO reading the epic. Carries
    no tombstone line: `death_marker` is posted as its own comment right after
    this one, because a record that shares a comment with prose is a record
    any prose can forge (`_sole_record`)."""
    return (
        f"🪦 **The post-approval review of {epic} did not finish** — it "
        f"{_death_sentence(row)}.\n\n"
        "This was not a rejection: the critic decided nothing, nothing has "
        "been found wrong with the plan, and nothing has started building — "
        "the children stay in Backlog until the review runs again. This round "
        f"does not count toward the {_count_word(MAX_ROUNDS)}-round bound.\n\n"
        f"**Nothing here is yours to decide.** {DEAD_REVIEW_NEXT}"
    )


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))


# --- The bound --------------------------------------------------------------

def _count_word(n: int) -> str:
    """`two failed rounds`, not `2 failed rounds` — the bound is a sentence a
    non-technical reader meets on their own epic (standards/comms.md). Falls
    back to the digit for any count the words do not cover, so raising
    MAX_ROUNDS can never produce a wrong word."""
    return {1: "one", 2: "two", 3: "three"}.get(n, str(n))


def _bound_spent(prior_send_backs: int) -> bool:
    """Does THIS send-back spend the last round of the budget?

    The one place the arithmetic lives, for all three routes (DRE-4058). The
    count is of PRIOR failed rounds, so the round being decided is `prior + 1`,
    and the bound is reached when that number reaches MAX_ROUNDS. Every reader
    below asks this rather than comparing against a literal, so raising or
    lowering MAX_ROUNDS moves the epic route and the one-off route together —
    which is what "the bound, on both loops" was always supposed to mean, and
    for two months did not.
    """
    return int(prior_send_backs) + 1 >= MAX_ROUNDS


def post_bound_spent(prior_send_backs: int, open_count: int | None) -> bool:
    """Does THIS post-approval send-back spend the budget (DRE-4115)?

    `open_count` is how many of the PREVIOUS round's findings this round found
    still open — the critic's `still-open:` line, or the marker's `open=`
    field when the sweep reads the same round back. Three readings:

      * `None` — no reading. A marker written before the field existed, or a
        caller with nothing to say about the previous round. Unknown is not
        "answered" (standards/console-honesty.md rule 2), so this is the
        arithmetic every round was parked under before: `_bound_spent`.
      * a finding still open — "sent back twice and still not fixed". The
        bound, on the second send-back, exactly as before.
      * every finding answered — the revision is judged on its own rounds. It
        is NOT the bound at round 2; it IS the bound once MAX_ROUNDS revisions
        have each answered everything and the review still finds new gaps,
        because a plan that keeps growing findings is not converging and a
        person should read it rather than the pipeline paying for a fourth
        review. Nothing circles forever.

    Round 1 never spends anything, whatever the reading says: there was no
    previous round to leave open.
    """
    prior = int(prior_send_backs)
    if prior < 1:
        return False
    if open_count is None:
        return _bound_spent(prior)
    return int(open_count) > 0 or prior >= MAX_ROUNDS


def decide(result: str, prior_send_backs: int, reason: str = "",
           stage: str = STAGE_PRE,
           still_open: list[str] | None = None) -> tuple[str, str]:
    """`(action, note)` — `hold` stops the plan here, `proceed` moves it on.

    The bound: the FIRST send-back holds; the second means two failed rounds.
    What the bound DOES depends on which side of the CEO the critic sits
    (DRE-3088):

      * PRE stage — the plan reaches the CEO regardless, with the critic's
        stated reason attached. "Proceed" here means "a person reads it", so
        proceeding on a held plan costs the CEO a read, nothing more.
      * POST stage — the plan PARKS. "Proceed" here means "agents build it",
        and a plan the critic held twice is exactly the specification that
        would make them build the wrong thing. So the second send-back holds
        as well, and the workflow parks the epic in Green Light with
        `needs-human` and both findings (the watched queue — not the unread
        lane the 27-day failure lived in).

    ...and on the POST stage "held twice" means held twice ON THE SAME
    FINDINGS (DRE-4115). `still_open` is which of the previous round's
    findings this round found still open — `[]` when the revision answered
    every one, `None` when the caller has no reading (then the count decides,
    as it always did). See `post_bound_spent` for the three readings and the
    cap that keeps a plan from circling forever.

    Nothing circles a third time on either side.
    """
    if result == PASS:
        return "proceed", "the critic passed this plan"
    if result != SEND_BACK:
        # Crash, empty file, unparseable header, reason-less send-back. The
        # critic did not decide, so it does not get to stop anything.
        return "proceed", (
            "the critic produced no result — a crash is not a rejection, so the "
            "plan proceeds and this round is not counted against the bound"
        )
    failed = prior_send_backs + 1
    if stage == STAGE_POST:
        return _decide_post(prior_send_backs, reason, still_open)
    if not _bound_spent(prior_send_backs):
        return "hold", f"sent back — round {failed} of {MAX_ROUNDS}"
    note = (
        f"{_count_word(failed)} failed rounds at this critic — the bound, so the "
        "plan proceeds to the CEO regardless rather than circling. "
        "The critic's stated reason, unresolved: "
    ) + one_line(reason)
    return "proceed", note


def _decide_post(prior: int, reason: str,
                 still_open: list[str] | None) -> tuple[str, str]:
    """The post stage's half of `decide`, for a real send-back."""
    failed = prior + 1
    open_count = None if still_open is None else len(still_open)
    if not post_bound_spent(prior, open_count):
        if prior < 1:
            return "hold", f"sent back — round {failed} of {MAX_ROUNDS}"
        return "hold", (
            f"sent back — round {failed} on this planning attempt, but the "
            "revision answered every finding of the round before, so this "
            "round is judged on its own: the plan is revised again for what "
            "was found now, and the review re-runs. One more send-back that "
            "the revision does not settle parks the plan for you"
        )
    if open_count is None:
        note = (
            f"{_count_word(failed)} failed rounds at this critic — the bound. "
            "This plan has been sent back twice since it was approved, so it "
            "parks for you with `needs-human` instead of being built as it "
            "stands. The critic's stated reason, unresolved: "
        ) + one_line(reason)
        return "hold", note
    if open_count > 0:
        listed = "; ".join(f"{i}. {f}" for i, f in enumerate(still_open, 1))
        note = (
            f"{_count_word(failed)} failed rounds at this critic and the "
            "revision did not settle it — the bound. Still open after the "
            f"revision: {listed}. The plan parks for you with `needs-human` "
            "instead of being built as it stands. The critic's newest "
            "finding: "
        ) + one_line(reason)
        return "hold", note
    note = (
        f"{_count_word(failed)} failed rounds at this critic — the bound. The "
        f"plan has been revised {_count_word(prior)} times since it was "
        "approved, each time answering everything the critic named, and the "
        "review still finds new gaps — it is not converging, so it parks for "
        "you with `needs-human` rather than circling. The critic's newest "
        "finding, unresolved: "
    ) + one_line(reason)
    return "hold", note


def at_bound(action: str, prior_send_backs: int, result: str,
             still_open: list[str] | None = None) -> bool:
    """Did THIS decision spend the last round of the budget? True only for a
    real send-back that holds at the bound — the post stage's park signal.
    A crash or a pass never reaches the bound, whatever the count says.
    `still_open` is the post stage's reading (DRE-4115); with none given the
    answer is the count's, exactly as before."""
    open_count = None if still_open is None else len(still_open)
    return (action == "hold" and result == SEND_BACK
            and post_bound_spent(prior_send_backs, open_count))


# --- The one-off exit (DRE-3041) --------------------------------------------
#
# The same critic, one call, and a decision that fails in the OPPOSITE
# direction to the one above. That inversion is the whole of the difference and
# it is not a contradiction of console-honesty rule 1:
#
#   on the EPIC route a crash is not a rejection, because the plan is on its way
#   to the CEO and a critic that decided nothing must not stop a human reading
#   it. Something else reads the plan after the critic.
#
#   on the ONE-OFF route NOTHING reads the card after this. A pass moves it to
#   the build queue and an agent builds it unattended, so "the critic did not
#   decide" cannot be spent as "the critic said yes". The card goes to a person
#   instead — the cheap outcome — and the run says which of the two happened.
#
# AND IT IS BOUNDED, on the same count as the epic route (DRE-4058). What stood
# here until 2026-09-16 was the claim that it needed no bound "because there is
# no loop to bound: one call per one-off classification, and the card either
# moves or parks". That premise was false, and the loop it missed is the
# expensive one:
#
#   the card parks in Green Light → the CEO answers it and moves it back to
#   Planning → that is a NEW one-off classification → a new single unbounded
#   call → which finds the NEXT problem and parks it again.
#
# One call per classification, and nothing limited the classifications. DRE-3879
# went round five times: five different findings, two signed console answers,
# rounds 4 and 5 six minutes apart on 2026-09-15 PT and both after the CEO's
# 13:12 PT answer. DRE-3880 did it three times. The findings were real and new —
# the critic reads the whole card and reports the worst of it — so each round
# bought a genuine finding at the price of a full CEO round trip, and the card
# never converged because each answer exposed the next gap.
#
# `round=N` was already on every marker and `send_backs` already counted them:
# the record was there and nothing read it. So this route now asks for the count
# and, at `_bound_spent`, answers a THIRD action. A card sent back MAX_ROUNDS
# times is not a card the next answer fixes — it needs REWRITING, which is a
# different request and has to be made as one, with every finding raised so far
# named in one place so a single rewrite can answer all of them.
#
# The budget is the CARD's whole history, not an attempt's: no `plan-cycle:`
# boundary is ever posted on this route, and none is wanted. The epic route
# needs one because a re-plan argues about a different document; here the card
# IS the document, and a spent bound blocks nothing that has actually been
# fixed — a PASS proceeds at any count (`one_off_decide` reads the verdict
# before it reads the budget), so a rewritten card that now passes goes to the
# build queue with nothing to refund.

#: The three actions the one-off exit can take. `proceed` runs
#: `planning_route.py exit`; `escalate` and `rewrite` both run
#: `planning_escalation.py escalate` — the same seam, because a card the critic
#: stopped parks in the CEO's queue either way — and are told apart because they
#: ask him for different things: an ANSWER, or a rewritten card. Distinct values
#: rather than one action with a flag, so the run's own output says which
#: happened and nothing downstream has to re-derive it.
PROCEED = "proceed"
ESCALATE = "escalate"
REWRITE = "rewrite"

#: What the run records when the critic produced nothing usable. Its own
#: sentence rather than the epic route's, because here it is not a shrug.
NO_CRITIC_NOTE = (
    "the critic produced no result, and on this route that is not a pass — "
    "nothing else reads this card before it is built, so it goes to a person"
)


def one_off_decide(result: str, reason: str = "",
                   prior_send_backs: int = 0) -> tuple[str, str]:
    """`(action, note)` for a one-off exit — `proceed` moves it, `escalate` asks
    the CEO a question, `rewrite` tells him the card itself has to change.

    Only a PASS moves the card, and it moves it at ANY count: the budget is
    spent by findings, not by the card, and a card that now passes has nothing
    left to answer. A SEND_BACK carries the critic's own line until the bound;
    at `_bound_spent` it carries the rewrite request instead. A crash, an empty
    file, an unparseable header, a reason-less send-back or a verdict this
    module does not write all land on the ordinary escalate and spend NOTHING —
    the epic route's rule (`decide`), for the same reason: a round the critic
    never decided is not a failed round, and must not be charged as one.

    `prior_send_backs` is what the card's own markers already record
    (`send_backs(current_cycle(thread), STAGE_ONE_OFF)`), so the number the
    bound reads is the number the run posted. It defaults to zero, which is the
    first round and the behaviour every caller had before DRE-4058.
    """
    if result == PASS:
        return PROCEED, (
            "the critic read this card and found one pull request of work an "
            "agent can build unattended"
        )
    if result == SEND_BACK and one_line(reason):
        if not _bound_spent(prior_send_backs):
            return ESCALATE, one_line(reason)
        return REWRITE, (
            f"{_count_word(int(prior_send_backs) + 1)} send-backs on this card "
            "— the bound. It needs REWRITING rather than another answer, and "
            "every finding raised so far is listed below so one rewrite can "
            "answer all of them."
        )
    return ESCALATE, NO_CRITIC_NOTE


#: What the list says in place of a finding whose own words are not fit to put
#: in front of the CEO. Its own LINE rather than a silent drop: a rewrite that
#: answers four findings out of five is another round, so the count has to
#: survive even where the wording cannot (`planning_escalation.jargon`).
FINDING_NOT_PLAIN_ENGLISH = (
    "One finding was written in technical terms, so it is not repeated here — "
    "it is in the run's own log."
)


def every_finding_so_far(prior_findings, this_round) -> list[str]:
    """Every finding this card has collected, oldest first, each once.

    The markers' spine (`send_back_findings`) followed by this round's ranked
    list (`all_findings`), deduplicated — a critic that re-raises a finding the
    CEO has already been shown must not make the list say it twice. Order is
    chronological rather than ranked, because the CEO reading it has answered
    the early ones and the question he is being asked is what is STILL open.

    Deliberately uncapped, unlike one round's list (MAX_FINDINGS): the bound is
    what limits the length, and a history that quietly dropped its oldest
    findings would buy the exact round this card exists to prevent.
    """
    out: list[str] = []
    for item in list(prior_findings or []) + list(this_round or []):
        flat = one_line(item)
        if flat and flat not in out:
            out.append(flat)
    return out


def _sayable_findings(findings) -> list[str]:
    """The findings as the CEO may be shown them, one line each.

    Every item is an AGENT's sentence, so each is read by the same seam that
    guards the single reason — per ITEM, because one leaking line must cost that
    line and never the list. The raw text stays in the run log for an operator.
    """
    import planning_escalation  # late: it reads planning_route, which reads us

    out = []
    for item in findings or []:
        leaks = planning_escalation.jargon(item)
        if not leaks:
            out.append(item)
            continue
        print("plan critic: a one-off finding is not fit for the card — "
              f"{', '.join(leaks)}\n--- the critic wrote ---\n{item}",
              file=sys.stderr)
        out.append(FINDING_NOT_PLAIN_ENGLISH)
    return out


def one_off_rewrite_request(prior_send_backs: int, findings) -> str:
    """What the CEO is handed when the one-off bound is spent (DRE-4058).

    Not a question about the work: a statement that the CARD is the problem,
    plus every finding raised so far in one place. DRE-3879's CEO answered five
    questions and was never once shown the other findings the same critic had
    already written down, so each answer could only ever close one of them.

    `standards/comms.md` all the same — it is still the CEO's queue this lands
    in: what happened, the list, and exactly one ask as the closing line.
    """
    trips = _count_word(int(prior_send_backs) + 1)
    said = _sayable_findings(findings)
    return "\n\n".join([
        f"This card has been round-tripped {trips} separate times — stopped, "
        "answered, and stopped again by a different problem. Another answer is "
        "not what it needs: the card itself has to be rewritten, and that is "
        "why this is not one more question about the work.",
        *([ "Everything found so far, so one rewrite can answer all of it:\n\n"
            + findings_block(said) ] if said else []),
        "Which gets to my question: do you want to rewrite this card yourself, "
        "or should we take it out of the queue and start again from a fresh one?",
    ])


def one_off_escalation(result: str, reason: str = "",
                       prior_send_backs: int = 0, findings=()) -> str:
    """The plain-English question the CEO is handed when a one-off does not pass.

    `standards/comms.md`: purpose first, the finding in its own block, and one
    ask as the closing line. It is written for a non-technical reader because
    it is the CEO's decision queue this lands in, and the reason half of it was
    written by an AGENT — so the same seam that guards the planner's own
    escalation text guards this one (`planning_escalation.jargon`). A reason
    that leaks a path or a command costs the REASON, never the question: the
    raw text stays in the run log, and the card still parks with something a
    person can answer.

    AT THE BOUND it is a different text, and WHICH ONE IS NOT DECIDED HERE
    (DRE-4058): `one_off_decide` is asked, on the same inputs, so the words the
    CEO reads and the action the run took can never disagree. `findings` is
    every finding raised so far (`every_finding_so_far`) and is read only on
    that branch — below the bound the card is still one question with one
    finding, which is what the CEO has always been handed.
    """
    import planning_escalation  # late: it reads planning_route, which reads us

    if one_off_decide(result, reason, prior_send_backs)[0] == REWRITE:
        return one_off_rewrite_request(prior_send_backs, findings)

    stated = one_line(reason)
    if result == SEND_BACK and stated and not planning_escalation.jargon(stated):
        finding = f"What it found: {stated}"
    elif result == SEND_BACK and stated:
        print("plan critic: the one-off reason is not fit for the card — "
              f"{planning_escalation.NOT_PLAIN_ENGLISH}\n--- the critic wrote "
              f"---\n{stated}", file=sys.stderr)
        finding = (
            "What it found was written in technical terms, so it is not "
            "repeated here — it is in the run's own log."
        )
    else:
        finding = (
            "What happened: the reader did not answer at all, so nothing has "
            "checked this card. We treat that as a stop rather than a yes, "
            "because after this point the work is simply built."
        )
    return "\n\n".join([
        "This card was about to go to the build queue, and the reader that "
        "checks work of this size did not think an agent could finish it "
        "unattended.",
        finding,
        "Which gets to my question: is this something you want to settle "
        "yourself, or should we put it back in the queue as it stands?",
    ])


# --- Cross-epic sight (D3) --------------------------------------------------

def sight_block(this_epic: str, epics: list[dict]) -> str:
    """What the post critic can see across epics, stated exactly.

    `rather than being told to "consider other work"`: the epics are named,
    and so is the boundary. The cost of this decision is a vaguer critic, and a
    vague scope is how that cost compounds — a critic that does not know what
    it was shown cannot tell you what it missed.
    """
    others = [e for e in (epics or [])
              if (e.get("identifier") or "") != this_epic]
    lines = [
        "CROSS-EPIC SCOPE — read this before you look for collisions.",
        "",
    ]
    if others:
        lines.append(
            f"YOU CAN SEE these {len(others)} other epic(s) in flight, and only "
            "these:"
        )
        for e in others:
            lines.append(
                f"  - {e.get('identifier')} — {e.get('title')} "
                f"[{e.get('state')}]"
            )
    else:
        lines.append(
            "YOU CAN SEE no other epic — nothing else is in flight right now, "
            "so a collision with another epic is not possible from what you "
            "were given."
        )
    lines += [
        "",
        "That list is every epic in "
        + ", ".join(IN_FLIGHT_EPIC_STATES)
        + " on the DRE board at the moment this run started.",
        "",
        "YOU CANNOT SEE, and must not claim anything about: epics in Backlog, "
        "Intake or Done; work in any other Linear team; unmerged branches and "
        "open pull requests; or anything an epic's own cards do not say. If a "
        "collision would need one of those to confirm, say what you suspect "
        "and say that you could not confirm it — never assert it.",
    ]
    return "\n".join(lines) + "\n"


# --- The first critic's cheap half ------------------------------------------

# `## Acceptance criteria` and at least one checkable item under it.
_ACCEPTANCE_HEADING = re.compile(r"^#{1,6}\s*acceptance\s+criteria\s*$",
                                 re.IGNORECASE | re.MULTILINE)
# WHICH marks count is `checkbox_marks`' answer, not a second one written here
# (DRE-3147): a card the route reads as carrying criteria and this check reads
# as carrying none is one card refused twice for the same non-reason.
_CHECK_ITEM = checkbox_marks.ITEM_MULTILINE

# The `repo:<slug>` LABEL, with a non-empty slug — the canonical and only
# source of truth for a card's repo (standards/card-quality.md, DRE-1699).
_REPO_LABEL = "repo:"


def cards_without_acceptance(cards: list[dict]) -> list[str]:
    """Cards with no OBSERVABLE acceptance criteria: no section, or a section
    with nothing checkable under it."""
    out = []
    for card in cards or []:
        body = card.get("body") or ""
        m = _ACCEPTANCE_HEADING.search(body)
        if not m or not _CHECK_ITEM.search(body[m.end():]):
            out.append(card.get("identifier"))
    return out


def cards_without_repo(cards: list[dict]) -> list[str]:
    """Cards carrying no `repo:<slug>` LABEL.

    The label is the contract (standards/card-quality.md rule 1) and the body
    stamp it replaced is explicitly deprecated — `briefs/planner.md` tells the
    planner "do NOT write a `**Repo:** <slug>` line". This used to be a body
    regex for exactly that forbidden line, so it flagged all five of DRE-3019's
    correctly-built children as "names no repo" and the critic passed anyway
    (DRE-3040). Five false findings is how a critic learns to skip the list, and
    the finding it skips next is a real one.
    """
    out = []
    for card in cards or []:
        labels = [str(l or "").strip().lower() for l in (card.get("labels") or [])]
        if not any(l.startswith(_REPO_LABEL) and l[len(_REPO_LABEL):].strip()
                   for l in labels):
            out.append(card.get("identifier"))
    return out


def shared_files(cards: list[dict]) -> dict[str, list[str]]:
    """Files DECLARED by more than one card, path → the cards declaring it.

    `Each card/agent owns DISJOINT files` (standards/engineering.md): a shared
    file edited by two open PRs conflicts every sibling.

    The declared footprint is the input, parsed once in `plan_footprint` — the
    same parser the ordering check consumes, because two regexes for one line
    are two answers waiting to disagree. Before DRE-3040 this scanned whole
    bodies with a path regex that required a `/`, which read every path
    mentioned in an acceptance criterion as a footprint and could not see
    `README.md` at all.

    DELIVERED CHILDREN ARE NOT IN THE INPUT (DRE-3243). The rule is about two
    OPEN pull requests racing for one file; a Done, Canceled or Duplicate card
    has already merged its half or dropped it, so it cannot conflict with a
    sibling. Leaving it in produced a note that said "it is not a collision
    with a sibling over a file it has already merged either" and then listed
    exactly that collision under `Findings` two paragraphs below — the same
    contradictory signal about a Done card that DRE-3243 exists to remove,
    relocated rather than fixed. `shipped_work_is_a_finding()` is the one
    definition of "delivered", shared with the state block that says so.
    """
    return plan_footprint.collisions(
        [c for c in cards or [] if shipped_work_is_a_finding(c)])


# --- A footprint that has died before (DRE-3079) -----------------------------
#
# `config/split-ledger.json` (DRE-3077) is the record of every card that did
# not fit one run: what it declared, what its split pieces actually touched,
# how many turn-cap deaths it cost and which of DRE-2893's tells applied in
# hindsight. Piece 2 injects it into the planner; this is the OTHER reader —
# the mechanical half of the first critic, checking a plan the planner has
# already written against the deaths the ledger already holds.

#: Where the ledger lives, for the finding to cite. Named here rather than
#: imported at module scope: `split_ledger` imports `planner_score`, which
#: imports THIS module, so the import is deferred to the one function that
#: needs it (see `_ledger`).
LEDGER_FILE = "config/split-ledger.json"

#: How many files a child must share with a ledger row before the overlap is
#: worth a finding. ONE shared file is the ordinary state of this repo —
#: almost every card touches a file some dead card also touched — and a check
#: that fires on every card is a label rather than a measurement. DRE-3040
#: measured what that costs: five false "names no repo" findings, and a critic
#: that learns to skip the list skips the real finding next.
LEDGER_MIN_OVERLAP = 2

#: What a caller passes for `ledger` when the file could not be read, and what
#: `_ledger` returns when its own read fails. A sentinel rather than `None`,
#: because `None` is "nothing passed, go and load it" and this is "the load
#: already failed" — a crash is not a clean sheet
#: (standards/console-honesty.md rule 1).
LEDGER_UNREADABLE = "LEDGER_UNREADABLE"

def _split_ledger():
    """The `split_ledger` module, imported late.

    `split_ledger` → `planner_score` → `plan_critic`, so importing it at module
    scope would close a cycle through this file. The same deferred-import
    pattern the live seams in this repo already use.
    """
    import split_ledger  # noqa: PLC0415 - deferred to break an import cycle

    return split_ledger


def _death_reasons() -> tuple:
    """The reasons a ledger row records the card DYING rather than merely being
    named, read off `split_ledger.DEATH_REASONS` at call time.

    A copy of the three strings here would keep matching the old spellings
    after a rename and report fewer rows with no error at all — the "checked
    and found nothing" that `LEDGER_UNREADABLE` exists to keep out of this
    reader (standards/console-honesty.md rule 1).
    """
    return _split_ledger().DEATH_REASONS


def _ledger(ledger=None):
    """The ledger to check against: what the caller passed, or the shipped
    file, or `LEDGER_UNREADABLE` when it could not be read."""
    if ledger is LEDGER_UNREADABLE:
        return LEDGER_UNREADABLE
    if ledger is not None:
        return ledger
    try:
        return _split_ledger().load()
    except Exception:                               # noqa: BLE001 - see below
        # Reported, never swallowed: `ledger_findings` turns this into a
        # finding of its own so an unread ledger is visible on the epic.
        return LEDGER_UNREADABLE


def _row_footprint(row: dict) -> tuple[set, str]:
    """A ledger row's footprint and where it came from.

    Two answers, in order. The row's DECLARED files are the card's own claim,
    and most rows carry none — those cards predate the `Files:` line — so the
    fallback is what the split pieces actually touched, which is the only
    footprint the ledger has for them. Every finding says which of the two it
    matched on, because a declaration and a reconstruction are different
    evidence and the reader weighs them differently.
    """
    for field, name in (("declared_files", "declared"), ("piece_files", "pieces")):
        value = row.get(field)
        # UNKNOWN is the ledger's literal for a field it could not read, and it
        # is a string — a list is the only shape that is an answer.
        if isinstance(value, list) and value:
            return {str(path) for path in value}, name
    return set(), ""


def ledger_death_rows(ledger=None) -> list[dict]:
    """The rows that record a card DYING — a turn-cap death, a split, or a
    hand-back.

    The ledger's population is wider than its deaths: a seed row that was named
    and then survived says nothing about a footprint, and reporting it would
    make the check fire on work that went fine.
    """
    doc = _ledger(ledger)
    if doc is LEDGER_UNREADABLE:
        return []
    death_reasons = _death_reasons()
    rows = []
    for row in doc.get("rows") or ():
        deaths = row.get("deaths")
        died = isinstance(deaths, int) and deaths > 0
        if died or any(r in death_reasons for r in row.get("reasons") or ()):
            rows.append(row)
    return rows


def ledger_footprint_matches(cards: list[dict], ledger=None) -> list[dict]:
    """Children whose declared footprint lands on a row that died.

    One entry per (card, row) pair, carrying the files they share and which of
    the row's two footprints matched. A card that declares no footprint is not
    matched here at all — it already has its own finding, and a match derived
    from an empty set would be invented.
    """
    declared = plan_footprint.footprints(cards)
    missing = set(plan_footprint.cards_without_footprint(cards))
    rows = ledger_death_rows(ledger)
    matches = []
    for card in cards or ():
        identifier = card.get("identifier")
        if identifier in missing:
            continue
        files = set(declared.get(identifier) or ())
        if not files:
            continue
        for row in rows:
            footprint, on = _row_footprint(row)
            shared = sorted(files & footprint)
            if len(shared) < LEDGER_MIN_OVERLAP:
                continue
            matches.append({
                "card": identifier,
                "row": row.get("card"),
                "on": on,
                "shared": shared,
                "deaths": row.get("deaths"),
                "reasons": list(row.get("reasons") or ()),
            })
    return matches


def ledger_tell_matches(cards: list[dict], ledger=None) -> list[dict]:
    """Children carrying a tell the ledger has watched kill cards.

    The rate is the ledger's own sentence (`rates.by_tell`), quoted rather than
    recomputed. A tell whose population never died is not reported: a rate of
    zero is evidence FOR the card, and printing it would pad the list the
    critic reads.
    """
    doc = _ledger(ledger)
    if doc is LEDGER_UNREADABLE:
        return []
    rates = {band.get("tell"): band
             for band in ((doc.get("rates") or {}).get("by_tell") or ())
             if isinstance(band.get("died"), int) and band["died"] > 0}
    ledger_module = _split_ledger()
    matches = []
    for card in cards or ():
        body = card.get("body") or ""
        evidence = ledger_module.tell_evidence(body)
        for tell in ledger_module.tells(body):
            band = rates.get(tell)
            if band:
                matches.append({"card": card.get("identifier"), "tell": tell,
                                "evidence": evidence.get(tell) or "",
                                "sentence": band.get("sentence") or ""})
    return matches


def ledger_findings(cards: list[dict], ledger=None) -> list[str]:
    """The split-ledger half of the mechanical checks, as finding lines.

    An unreadable ledger is its OWN finding and never an empty list: "checked
    against the ledger and found nothing" and "never read the ledger" are
    different facts, and only one of them clears a plan
    (standards/console-honesty.md rule 1).
    """
    if _ledger(ledger) is LEDGER_UNREADABLE:
        return [f"the split ledger ({LEDGER_FILE}) could not be read, so no "
                "card in this plan was checked against a footprint that has "
                "died before"]
    findings = []
    for match in ledger_footprint_matches(cards, ledger):
        deaths = match["deaths"] if isinstance(match["deaths"], int) else "an unread number of"
        where = ("the files it declared" if match["on"] == "declared"
                 else "the files its split pieces touched")
        findings.append(
            f"{match['card']}: shares {len(match['shared'])} file(s) with "
            f"{match['row']}, which the split ledger records dying {deaths} "
            f"time(s) — {', '.join(match['shared'])} (matched against {where}; "
            f"the row is in {LEDGER_FILE})"
        )
    for match in ledger_tell_matches(cards, ledger):
        # The EVIDENCE travels with the tell, because the tell reader
        # under-reports by design and a critic that cannot see why one fired
        # cannot weigh it — nor see that the rate behind it is 2 of 2.
        because = f" ({match['evidence']})" if match["evidence"] else ""
        findings.append(
            f"{match['card']}: carries the {match['tell']} tell{because} — the "
            f"split ledger says {match['sentence']} ({LEDGER_FILE})"
        )
    return findings


def mechanical_findings(cards: list[dict], plan_comment: str = "",
                        surfaces: list[str] | None = None,
                        ledger=None) -> list[str]:
    """The structural defects the first critic never has to think about.

    Cheap, deterministic, and run BEFORE the critic spends a turn: a card with
    no acceptance criteria is a finding whatever the plan says about it.
    """
    findings = []
    for ident in cards_without_acceptance(cards):
        findings.append(f"{ident}: no observable acceptance criteria")
    for ident in cards_without_repo(cards):
        findings.append(f"{ident}: names no repo")
    # A card that declares no footprint is a REFUSAL, never a silent empty set:
    # the ordering was supposed to be derived from that line, and a card with
    # no line cannot be checked for a collision at all.
    for ident in plan_footprint.cards_without_footprint(cards):
        findings.append(
            f"{ident}: declares no file footprint — the `**Files:**` line is the "
            "input to the ordering, so this card cannot be checked for collisions"
        )
    for path, ids in sorted(shared_files(cards).items()):
        findings.append(f"{path}: touched by {', '.join(ids)} — siblings must own disjoint files")
    for surface in unaccounted_surfaces(
        list(surfaces or []), [c.get("body") or "" for c in cards or []], plan_comment or ""
    ):
        findings.append(
            f"{surface}: designed but no card carries it and the plan does not defer it"
        )
    # DRE-3079: the same list, against the deaths already on the board.
    findings += ledger_findings(cards, ledger)
    return findings


def _ledger_line(ledger=None) -> str:
    """One line saying what the ledger check had to read, for the note.

    Rule 2 again: "checked against 7 death rows and matched none" and "never
    opened the ledger" are different facts, and the count is the only thing
    that tells them apart.
    """
    if _ledger(ledger) is LEDGER_UNREADABLE:
        return (f"The split ledger (`{LEDGER_FILE}`) **could not be read**, so "
                "no footprint here was checked against a card that has died.")
    rows = ledger_death_rows(ledger)
    return (f"Checked against the split ledger (`{LEDGER_FILE}`): "
            f"{len(rows)} death row(s).")


def _state_block(cards: list[dict]) -> list[str]:
    """The children's lanes, and what a delivered one means, for the note.

    A NON-FINDING said out loud (DRE-3243). The findings below it are things
    the plan has to fix; this says which cards cannot be one, so a model that
    reads "already implemented on `main`" off the tree does not re-raise it —
    under that name or as a collision.
    """
    lines = ["Each card's STATE, which is what says whether it is to-build at "
             "all:"]
    if not any(child_state(c) for c in cards or []):
        # Rule 2 again: "no child is delivered" and "nothing told us" are
        # different facts, and only the first excuses nothing.
        lines.append("- **no child carried a state** — the children were read "
                     "without one, so nothing here distinguishes a delivered "
                     "card from one still to build")
        return lines
    for card in cards or []:
        state = child_state(card) or "unknown"
        lines.append(f"- {card.get('identifier')}: {state}")
    delivered = delivered_children(cards)
    if delivered:
        named = ", ".join(f"{i} ({s})" for i, s in delivered)
        lines += [
            "",
            f"**Not a finding — delivered child:** {named}. A child in Done, "
            "Canceled or Duplicate is delivered or dropped, never to-build. A "
            "Done card describes the work it delivered, so its deliverable "
            "already existing on `main` is not a gap in this plan — and it is "
            "not a collision with a sibling over a file it has already merged "
            "either.",
        ]
    in_flight = in_flight_children(cards)
    if in_flight:
        named = ", ".join(f"{i} ({s})" for i, s in in_flight)
        lines += [
            "",
            f"In flight: {named}. Judge these on what they will land, not on "
            "whether their files are in the tree yet.",
        ]
    return lines


def findings_note(cards: list[dict], findings: list[str], ledger=None) -> str:
    """The mechanical half's own comment, posted to the epic BEFORE the critic
    reads it (DRE-3040).

    This check used to run inside the critic's turn, where the action's log
    reads "full output hidden for security" — so whether the critic weighed
    five findings or never saw them could not be read off the run at all, and
    DRE-3019's plan passed with `collisions=0` on a check that could not have
    found one. The record now outlives the run, on the epic, where a pass with
    unread findings is visible.

    It leads with the FOOTPRINT it checked, because "the check ran and found
    nothing" and "the check had nothing to read" are different facts and only
    the footprint tells them apart (standards/console-honesty.md rule 2).
    """
    declared = plan_footprint.footprints(cards)
    missing = set(plan_footprint.cards_without_footprint(cards))
    lines = [
        f"🔎 **Mechanical plan checks** — {len(cards or [])} card(s), run before "
        "the critic reads the plan. These are the INPUT to its judgement, not a "
        "verdict of their own.",
        "",
        "The declared footprint (`**Files:**`), which is what the collision "
        "check reads:",
    ]
    for card in cards or []:
        ident = card.get("identifier")
        if ident in missing:
            lines.append(f"- {ident}: **no `Files:` section** — nothing to check")
        else:
            files = sorted(declared.get(ident) or [])
            lines.append(f"- {ident}: " + (", ".join(files) if files
                                           else "declares no files"))
    lines.append("")
    lines += _state_block(cards)
    lines.append("")
    lines.append(_ledger_line(ledger))
    lines.append("")
    if findings:
        lines.append(f"Findings ({len(findings)}):")
        lines += [f"- {f}" for f in findings]
    else:
        lines.append("No structural findings.")
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------

def _stdin_json(default):
    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _read(path: str | None) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        # A missing result file is the crash case, and the crash case is a
        # normal outcome here — never an exception that fails the step.
        return ""


def _write_outputs(path: str | None, pairs: list[tuple[str, str]],
                   limit: int = 300) -> None:
    """One line per key. `limit` is `one_line`'s: the collapse to ONE line is
    the safety property (a note an agent wrote must never smuggle a second
    output key); the length is only a courtesy to whoever reads the log, and
    the decision's `note` — which since DRE-4115 names every finding still
    open — is written with a wider one so the park comment built from it is
    not cut mid-list."""
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in pairs:
                f.write(f"{key}={one_line(value, limit)}\n")
    except OSError as exc:
        print(f"plan critic: could not write step outputs: {exc}")


def _write_block_output(path: str | None, name: str, value: str) -> None:
    """The findings list as a step output — the one value here that may span
    lines, because the re-plan's prompt is handed the list AS a list.

    A one-line list is written as a one-line output, so the "one line per
    output" shape every other value has is what a single-finding send-back
    still produces. Only a genuinely multi-line list takes the heredoc, and it
    takes the one this pipeline already uses for agent-written text: a random,
    collision-checked delimiter (`sanitize_untrusted._write_output`, the seam
    `pr_size_strategy` reuses), so critic text can neither close the block
    early nor define an output of the workflow's own.
    """
    if not path or not value:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            if "\n" in value:
                # Late, and only on the branch that needs it: everything else
                # this module does stays importable beside its parsers alone.
                from sanitize_untrusted import _write_output
                _write_output(f, name, value)
            else:
                f.write(f"{name}={one_line(value)}\n")
    except OSError as exc:
        print(f"plan critic: could not write the findings block: {exc}")


def _cmd_prior_round(args) -> int:
    """The previous round's findings block for the critic's charter, or
    nothing on a first round (DRE-4115). Always 0: a block that could not be
    built is an empty block — the critic then runs as a first round would,
    and the decision's fallback reading still bounds it."""
    try:
        thread = _stdin_json([])
        # The ENTRIES, not the bodies: the round's clock lives on the record
        # and the block's `when` sentence is built from it (DRE-4115).
        found = prior_round(current_cycle_entries(thread, args.epic), args.stage)
        block = (prior_round_block(found["findings"], found.get("ran_at"))
                 if found else "")
    except Exception as exc:  # noqa: BLE001 — see the docstring
        print(f"plan critic: could not read the previous round ({exc}) — "
              "showing the critic nothing", file=sys.stderr)
        block = ""
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(block + ("\n" if block else ""))
    print(block, end="\n" if block else "")
    return 0


def _cmd_activate_cycle(args) -> int:
    """`open` or `keep`, for the route step's ACTIVATE branch (DRE-4115).
    Always 0, and an unreadable thread is `keep`: the direction that keeps a
    parked epic parked rather than refunding a budget nobody read."""
    try:
        answer = opens_fresh_attempt(_stdin_json([]), args.epic, args.reason)
    except Exception as exc:  # noqa: BLE001 — see the docstring
        print(f"plan critic: could not read the thread ({exc}) — keeping the "
              "current attempt", file=sys.stderr)
        answer = False
    print("open" if answer else "keep")
    return 0


def _cmd_charter(args) -> int:
    print(charter(args.stage, sight=_read(args.sight_file),
                  prior=_read(args.prior_file)), end="")
    return 0


def _cmd_mechanical(args) -> int:
    cards = _stdin_json([])
    surfaces = []
    if args.surfaces_dir and os.path.isdir(args.surfaces_dir):
        for root, _dirs, files in os.walk(args.surfaces_dir):
            surfaces += [os.path.join(root, f) for f in files
                         if f.lower().endswith(".png")]
    # Read ONCE and handed to both, so the list in the log and the list on the
    # epic cannot be checked against two different ledgers.
    ledger = _ledger()
    findings = mechanical_findings(cards, _read(args.plan_comment_file),
                                   sorted(surfaces), ledger=ledger)
    if args.note_file:
        # The comment the rail posts to the epic before the critic reads it.
        with open(args.note_file, "w", encoding="utf-8") as f:
            f.write(findings_note(cards, findings, ledger=ledger) + "\n")
    if not findings:
        print("no structural findings — "
              f"{len(cards)} card(s), {len(surfaces)} designed surface(s) in scope")
    for f in findings:
        print(f)
    # Always 0: this is INPUT to a critic, not a gate of its own. The critic
    # decides what a finding is worth.
    return 0


def _cmd_decide(args) -> int:
    thread = _stdin_json([])
    result_text = _read(args.result_file)
    result, reason = read_result(result_text)
    collisions = collisions_declared(result_text)
    # Every finding this round has, ranked (DRE-3251). `reason` is still the
    # first of them and still the only one the marker carries.
    items = all_findings(result_text)
    # The budget, and the round number, belong to THIS planning attempt. A
    # re-planned epic counted against the whole thread would spend a budget it
    # never used, and say "round 3 of 2" while doing it.
    cycle = current_cycle(thread, args.epic)
    prior = send_backs(cycle, args.stage)
    # The post stage's reading of the round before (DRE-4115): which of its
    # findings the revision left open, off the critic's `still-open:` line
    # against the list it was shown — the same list `prior-round` printed
    # into its charter, read back out of the same thread. None on a first
    # round: nothing came before, so there is nothing to have answered.
    still_open: list[str] | None = None
    if args.stage == STAGE_POST and prior:
        still_open = still_open_findings(result_text,
                                         prior_findings(cycle, args.stage), items)
    open_count = None if still_open is None else len(still_open)
    if args.stage == STAGE_ONE_OFF:
        # The bound on THIS route is the card's whole send-back history: the
        # loop runs through the CEO, so `prior` is the number of times he has
        # already been asked (DRE-4058). Read from the markers the earlier runs
        # posted — the same number, not a second count — which is why the round
        # it posts below and the budget it spends here cannot drift apart.
        #
        # `items` is this round's ranked list; every finding the CARD has
        # collected is that plus the spine of the markers, and the rewrite
        # request is the only text that needs the whole of it.
        action, note = one_off_decide(result, reason, prior)
        if action == REWRITE:
            items = every_finding_so_far(
                send_back_findings(cycle, args.stage), items)
    else:
        action, note = decide(result, prior, reason, stage=args.stage,
                              still_open=still_open)
    stats = rate(cycle, args.stage)
    # The round NUMBER counts every round this stage has run, including ones it
    # passed or crashed on; the BOUND counts only the failed ones. Two different
    # questions, and conflating them would spend the budget on a crash.
    round_n = stats["rounds"] + 1
    # `bound` is the workflow's park signal (DRE-3088): a post-stage hold at
    # the last round parks the epic with `needs-human` instead of asking the
    # CEO to approve the same plan a third time. A one-off REWRITE is the same
    # fact on the other route and says so here rather than leaving the run
    # claiming `bound=false` beside a note that says "the bound" (DRE-4058).
    bound = action == REWRITE or at_bound(action, prior, result, still_open)

    _write_outputs(args.github_output, [
        ("action", action),
        ("result", result),
        ("reason", reason),
        ("round", str(round_n)),
        ("collisions", str(collisions)),
        ("bound", "true" if bound else "false"),
        ("findings_count", str(len(items))),
        # The previous round's findings this round found still open, for the
        # park note (DRE-4115) — "none" when the revision answered every one,
        # and "unread" on a round that had no previous round to read.
        ("open", ("unread" if still_open is None
                  else "; ".join(still_open) or "none")),
        ("open_count", "" if open_count is None else str(open_count)),
    ])
    # The note, wider: the park comment is built from it and it names every
    # finding still open (DRE-4115). Still ONE line.
    _write_outputs(args.github_output, [("note", note)], limit=2000)
    # The list itself, for the re-plan's prompt — the ONE multi-line output.
    _write_block_output(args.github_output, "findings", findings_block(items))

    title = STAGES[args.stage]["title"]
    # 📝 for the rewrite park: it is not the 🙋 of a question the CEO can answer
    # where he sits, and a reader scanning the thread should be able to tell the
    # two apart without reading either (DRE-4058). Read from the module that
    # writes the park comment ITSELF, one line below this note on the card —
    # this note and that park opening with different icons is the drift the icon
    # was for. In its own branch rather than in the map: the map is built on
    # EVERY route, and `_cmd_decide` must not need the escalation module to
    # decide an epic round. REWRITE reaches only the one-off route, which needs
    # that module anyway (`one_off_escalation`).
    if result == NO_RESULT:
        icon = "⚠️"
    elif action == REWRITE:
        import planning_escalation  # late: it reads planning_route, which reads us

        icon = planning_escalation.REWRITE_MARK
    else:
        icon = {"hold": "🛑", "proceed": "✅", ESCALATE: "🙋"}[action]
    seen = stats["rounds"]
    rate_text = (
        f"send-back rate at this critic so far on this planning attempt: "
        f"{stats['send_backs']}/{seen} rounds"
        if seen
        else "send-back rate at this critic so far on this planning attempt: "
             "first round"
    )
    # TWO comments, never one. The note is for the CEO reading the epic; the
    # record is this gate's credential and says nothing else, because a record
    # sharing a comment with prose is a record that prose can forge
    # (`_sole_record`).
    #
    # The one-off route's note does not print "round N of 2": the bound it lives
    # under is the card's whole history rather than an attempt's, and the note
    # that matters is what the card needs next, which the decision already says
    # in words (DRE-4058 — until then this comment claimed the route had no
    # bound at all, which was the defect written down).
    if args.stage == STAGE_ONE_OFF:
        headline = f"{icon} **{title}** — {note}"
        # ...and what happens next, in the words of the route it is on. The
        # send-back RATE belongs to a planning attempt and this card has none.
        closing = (
            "This card goes to the build queue."
            if action == PROCEED
            else "This card is not going to the build queue — and it is not "
                 "waiting on an answer either. It needs rewriting so that one "
                 "revision answers every finding above; move it back once it "
                 "does, or take it out of the queue."
            if action == REWRITE
            else "This card is not going to the build queue — it is with a "
                 "person, in the decision queue, with the reason above."
        )
    elif args.stage == STAGE_POST:
        # No "of MAX_ROUNDS" here (DRE-4115): the post stage's rounds run one
        # per job across re-plans, and "round 6 of 2" beside "5/5 rounds" was
        # the contradiction the live epics printed. The note says what the
        # round means; the number says which round it is.
        headline = f"{icon} **{title}** — round {round_n}: {note}"
        closing = rate_text
    else:
        headline = f"{icon} **{title}** — round {round_n} of {MAX_ROUNDS}: {note}"
        closing = rate_text
    # The whole list rides HERE, beside the record and never in it: the note is
    # prose, and prose records nothing (`_sole_record`). This is the half of the
    # round the CEO reads, so it carries everything the critic found — and at the
    # one-off bound that is everything the CARD has collected, under a heading
    # that says so rather than claiming one round found it all.
    section = (findings_so_far_section(items) if action == REWRITE
               else findings_section(items))
    body = "\n\n".join([
        headline,
        *( [f"Reason: {reason}"] if reason and reason != note else [] ),
        *( [section] if section else [] ),
        closing,
    ])
    # The post record carries the reading (DRE-4115) so the sweep's gate reads
    # the round the way this step decided it; the other stages have none.
    record = marker(args.stage, round_n, result, reason, collisions,
                    open_count=open_count if args.stage == STAGE_POST else None)
    if args.note_file:
        with open(args.note_file, "w", encoding="utf-8") as f:
            f.write(body + "\n")
    if args.record_file:
        with open(args.record_file, "w", encoding="utf-8") as f:
            f.write(record + "\n")
    # Only when the card does NOT pass, and only on the route that has an
    # escalation exit: a reason file left behind by a passing card is a
    # question nobody owes an answer to, and the step that reads it is gated on
    # the action rather than on the file existing. BOTH parks write it — the
    # question and the rewrite request park through the same seam (DRE-2848's,
    # never a second one), and which text lands is decided inside
    # `one_off_escalation` off the same inputs the action came from.
    if (args.escalation_file and args.stage == STAGE_ONE_OFF
            and action in (ESCALATE, REWRITE)):
        with open(args.escalation_file, "w", encoding="utf-8") as f:
            f.write(one_off_escalation(result, reason, prior, items) + "\n")
    print(body)
    print()
    print(record)
    return 0


def _cmd_read_result(args) -> int:
    """Which of the three verdicts is in a critic's result file (DRE-3501).

    The step that reads this answer decides whether the review DIED, so it
    reads the FILE rather than the action's step outcome: on run 34144302622
    the review finished at turn 51 of a 48-turn ceiling, the action marked its
    step failed, and a `PLAN-CRITIC: PASS` nobody read was buried under a
    tombstone. `read_result`, not a second parser — the decision step reads
    the same file through the same function, and two readings of one file is
    how they would come to disagree.

    ALWAYS 0, and a missing file is `NO_RESULT`: the whole point is to answer
    about a run that may have crashed, and an answer that crashes with it
    would leave the rail exactly where it started.
    """
    result, _reason = read_result(_read(args.result_file))
    _write_outputs(args.github_output, [("verdict", result)])
    print(f"the second critic's result file says: {result}")
    return 0


def _cmd_sight(args) -> int:
    epics = _stdin_json([])
    print(sight_block(args.this, epics), end="")
    return 0


def _cmd_rate(args) -> int:
    print(json.dumps(rate(_stdin_json([]), args.stage)))
    return 0


def _cmd_collisions(_args) -> int:
    print(json.dumps(collision_counts(_stdin_json([]))))
    return 0


def _cmd_cycle_start(args) -> int:
    print(cycle_marker(args.epic) if args.record else cycle_start_note(args.epic))
    return 0


def _cmd_late_collision(args) -> int:
    print(late_collision_marker(args.epic, args.with_epic, args.detail))
    return 0


def _cmd_post_turns(args) -> int:
    """The review's ceiling as a step output. Always 0: a sizing that failed
    must degrade to the default, never wedge the review (pr_size_strategy's
    rule — and the workflow carries a static fallback on top of this)."""
    turns = post_review_turns(args.children)
    _write_outputs(args.github_output, [("max_turns", str(turns))])
    print(f"post-approval review ceiling: {turns} turns "
          f"(children={args.children!r})")
    return 0


def _cmd_review_turns(args) -> int:
    """The receipt for one post-approval review — what it SPENT against what
    it was given (DRE-3498). Reads the run's execution file through the one
    loader every result gate uses (`execution_result.load_execution` +
    `spend_scalars`), never a second parser, and prints ONE line for its own
    comment. Always 0, and never raises: a receipt that cannot be written must
    not change the review's outcome, so an unreadable file is `spent=?`."""
    execution = execution_result.load_execution(args.execution_file) \
        if args.execution_file else None
    scalars = execution_result.spend_scalars(execution)
    print(review_turns_marker(scalars.get("num_turns"), args.ceiling,
                              args.children, args.model))
    return 0


def _cmd_died(args) -> int:
    """The tombstone and its note, for the workflow step that runs only when
    the review step itself failed. Reads what the action wrote about the
    death through the one loader both result gates use
    (`execution_result.load_execution`) — never a second parser — and prints
    only numbers and the action's own subtype enum from it. Always 0: this
    step is the record of a failure, not a second one."""
    execution = execution_result.load_execution(args.execution_file) \
        if args.execution_file else None
    scalars = execution_result.spend_scalars(execution)
    subtype = execution.get("subtype") if isinstance(execution, dict) else None
    record = death_marker(args.stage, args.run, args.attempt, args.step,
                          subtype, scalars.get("num_turns"), args.ceiling)
    row = parse_deaths([record])[0]
    note = death_note(args.epic, row)
    if args.note_file:
        with open(args.note_file, "w", encoding="utf-8") as f:
            f.write(note + "\n")
    if args.record_file:
        with open(args.record_file, "w", encoding="utf-8") as f:
            f.write(record + "\n")
    print(note)
    print()
    print(record)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("charter", help="print a stage's prompt block")
    c.add_argument("stage", choices=sorted(STAGES))
    c.add_argument("--sight-file", default=None)
    # The previous round's findings block (`prior-round`), post stage only.
    c.add_argument("--prior-file", default=None)
    c.set_defaults(fn=_cmd_charter)

    q = sub.add_parser("prior-round",
                       help="the previous send-back round's findings block; thread on stdin")
    q.add_argument("--stage", required=True, choices=sorted(STAGES))
    q.add_argument("--epic", default=None)
    q.add_argument("--out", default=None, help="write the block here too")
    q.set_defaults(fn=_cmd_prior_round)

    a = sub.add_parser("activate-cycle",
                       help="open or keep the planning attempt on an ACTIVATE run; thread on stdin")
    a.add_argument("--epic", required=True)
    # Why the run was asked for — the payload's `reason`, empty on the CEO's
    # own approval move.
    a.add_argument("--reason", default="")
    a.set_defaults(fn=_cmd_activate_cycle)

    m = sub.add_parser("mechanical", help="structural findings; cards on stdin")
    m.add_argument("--plan-comment-file", default=None)
    m.add_argument("--surfaces-dir", default=None)
    # The findings as a comment for the epic, so the list exists somewhere the
    # critic's own hidden turn output cannot be the only record of it.
    m.add_argument("--note-file", default=None)
    m.set_defaults(fn=_cmd_mechanical)

    d = sub.add_parser("decide", help="apply the bound; comment thread on stdin")
    d.add_argument("--stage", required=True, choices=sorted(STAGES))
    # The epic this decision is about, so a `plan-cycle:` boundary naming a
    # DIFFERENT epic cannot refund this one's budget. Optional: a caller with
    # no epic in hand keeps the old any-boundary behaviour.
    d.add_argument("--epic", default=None)
    d.add_argument("--result-file", required=True)
    d.add_argument("--github-output", default=None)
    d.add_argument("--note-file", default=None)
    # The round record, for its OWN comment. Keeping it out of the note is what
    # makes "the pipeline wrote a bare marker" mean something (`_sole_record`).
    d.add_argument("--record-file", default=None)
    # The one-off route's CEO-facing reason, written only when the card does not
    # pass — `planning_escalation.py escalate --reason-file` reads it.
    d.add_argument("--escalation-file", default=None)
    d.set_defaults(fn=_cmd_decide)

    v = sub.add_parser("read-result",
                       help="which verdict a critic's result file holds")
    v.add_argument("--result-file", required=True)
    v.add_argument("--github-output", default=None)
    v.set_defaults(fn=_cmd_read_result)

    s = sub.add_parser("sight", help="cross-epic scope; epics on stdin")
    s.add_argument("--this", required=True)
    s.set_defaults(fn=_cmd_sight)

    r = sub.add_parser("rate", help="send-back rate; comment thread on stdin")
    r.add_argument("--stage", required=True, choices=sorted(STAGES))
    r.set_defaults(fn=_cmd_rate)

    y = sub.add_parser("cycle-start", help="the note that opens a planning attempt")
    y.add_argument("--epic", required=True)
    y.add_argument("--record", action="store_true",
                   help="print the boundary line alone, for its own comment")
    y.set_defaults(fn=_cmd_cycle_start)

    x = sub.add_parser("collisions", help="both collision counters; thread on stdin")
    x.set_defaults(fn=_cmd_collisions)

    l = sub.add_parser("late-collision", help="the marker for a collision found later")
    l.add_argument("--epic", required=True)
    l.add_argument("--with", dest="with_epic", required=True)
    l.add_argument("--detail", required=True)
    l.set_defaults(fn=_cmd_late_collision)

    t = sub.add_parser("post-turns",
                       help="the post-approval review's turn ceiling, sized from the plan")
    # A string on purpose: the workflow hands over whatever `linear_ops.py
    # children` printed, and an empty or unreadable count must size to the
    # default rather than fail the argument parse.
    t.add_argument("--children", default="")
    t.add_argument("--github-output", default=None)
    t.set_defaults(fn=_cmd_post_turns)

    w = sub.add_parser("review-turns",
                       help="what one post-approval review spent, as one line")
    w.add_argument("--execution-file", default=None)
    # Strings on purpose, exactly as `post-turns --children` is: the workflow
    # hands over whatever the ceiling step and `linear_ops.py children`
    # printed, and an empty or unreadable value must read as `?` rather than
    # fail the argument parse.
    w.add_argument("--ceiling", default="")
    w.add_argument("--children", default="")
    w.add_argument("--model", default="")
    w.set_defaults(fn=_cmd_review_turns)

    g = sub.add_parser("died", help="the tombstone for a review that ended without deciding")
    g.add_argument("--stage", required=True, choices=sorted(STAGES))
    g.add_argument("--epic", required=True)
    g.add_argument("--run", required=True)
    g.add_argument("--attempt", default="")
    g.add_argument("--step", required=True)
    g.add_argument("--ceiling", default="")
    g.add_argument("--execution-file", default=None)
    g.add_argument("--note-file", default=None)
    # The record, for its OWN comment — same reason as `decide --record-file`.
    g.add_argument("--record-file", default=None)
    g.set_defaults(fn=_cmd_died)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
