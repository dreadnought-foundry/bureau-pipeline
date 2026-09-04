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
    rounds argued about no longer exists.
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
  charter <stage> [--sight-file F]   the stage's prompt block
  mechanical [--plan-comment-file F] [--surfaces-dir D] [--note-file F]
                                     cards on stdin (`children-json`); the note
                                     is the list posted to the epic BEFORE the
                                     critic reads it
  decide --stage S --result-file F [--epic E] [--github-output F]
         [--note-file F] [--record-file F] [--escalation-file F]
                                     comment thread (JSON array) on stdin,
                                     from `dump-comments --with-authors`.
                                     The note and the record are TWO comments.
                                     `--escalation-file` is the one-off stage's
                                     CEO-facing reason, written only when the
                                     card does not pass.
  sight --this <EPIC>                epics in flight (JSON array) on stdin
  cycle-start --epic <EPIC> [--record]
                                     the note that opens a planning attempt,
                                     and (--record) the boundary line itself —
                                     again two comments, never one
  rate --stage S                     comment thread on stdin
  collisions                         comment thread on stdin
  late-collision --epic E --with E2 --detail "…"   print the marker line
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import design_parity
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

# The lanes an epic occupies while it is in flight (config/lane-contract.json).
# What the post critic can see is exactly this, and its charter says so.
IN_FLIGHT_EPIC_STATES = ("Green Light", "Todo", "In Progress")


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

SENDING IT BACK SHOULD BE RARE. How often you send a plan back is the honest
measure of how good the first critic is — so when you do, the reason has to be
specific enough that the first critic could have caught it.

WHEN YOU FIND COLLISIONS, write `collisions: <n>` on its own line in your
result file, naming each one in your reason. That count is recorded separately
from collisions found later, and the gap between the two is the tripwire for
whether this check needs its own pass.
{sight}"""

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


def charter(stage: str, sight: str = "") -> str:
    """The stage's prompt block, as the workflow interpolates it.

    `sight` is the cross-epic scope block and reaches the POST stage only: the
    other two charters state they have no cross-epic sight, and handing one to
    them would be the same critic twice.
    """
    spec = STAGES[stage]
    if stage != STAGE_POST:
        return spec["template"].format(question=spec["question"])
    return spec["template"].format(
        question=spec["question"],
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
           collisions: int = 0) -> str:
    """The machine-parseable record of one critic round."""
    line = (f"{MARKER_PREFIX} stage={stage} round={int(round_n)} "
            f"result={result} collisions={int(collisions)}")
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
        writes one, so only its own comments are read (`trusted_bodies`).
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
    bodies = trusted_bodies(bodies)
    start = 0
    for i, body in enumerate(bodies):
        # A body that is exactly a boundary cannot also be exactly a marker, so
        # the old "a marker never opens a cycle" guard is now structural.
        m = _sole_record(_CYCLE, body)
        if not m:
            continue
        if epic and m.group("epic") != epic:
            continue
        start = i + 1
    return bodies[start:]


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

#: The lane a CEO moves an epic to in order to APPROVE its plan. Approval is
#: the In Progress entry (the relay dispatches the activation on it; an epic
#: in Todo dispatches nothing — DRE-2725), and the contract's Green Light exit
#: clause is written in the same terms. Every receipt that asks the CEO to
#: approve again names THIS, so the instruction can never point at the one
#: lane where an epic sits forever. `tests/test_plan_critic_wiring.py` pins it
#: to a live lane in config/lane-contract.json.
APPROVAL_LANE = "In Progress"

#: Idempotency tags for the two refusals, in the `dead_run.DEAD_TAG` shape the
#: sweep already surfaces refusals under. TWO of them, deliberately: the sweep
#: posts each refusal at most once per tag, and "nobody has read this plan" and
#: "the critic found a gap" are different facts with different next actions —
#: one tag would let the first silence the second forever.
POST_UNREAD_TAG = "plan-critic-post-unread"
POST_SENT_BACK_TAG = "plan-critic-post-sent-back"

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
    """
    rows = [r for r in parse_markers(current_cycle(bodies, epic))
            if r["stage"] == STAGE_POST]
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
    if len(failed) >= MAX_ROUNDS:
        reasons = "; ".join(r["reason"] or "none given" for r in failed)
        return POST_HELD, (
            f"{_count_word(len(failed))} failed rounds at the second critic — "
            "the bound, so the plan is parked for the CEO with needs-human "
            "rather than built as it stands. The critic's stated reasons, "
            "unresolved: " + reasons
        )
    return POST_HELD, last["reason"]


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
            f"**To let it through:** approve the epic again by moving it to "
            f"{APPROVAL_LANE}. That re-runs the post-approval review, and the "
            "children promote on the next sweep once it passes."
        )
    quoted = one_line(detail) or "no reason recorded"
    return (
        f"🚨 {POST_SENT_BACK_TAG}: {identifier}'s epic {epic} was approved at "
        f"{when} but the second critic sent the plan back — holding. The "
        f"critic's reason: {quoted}\n\n"
        "The children stay in Backlog until the gap is settled. The plan was "
        "revised with the critic's finding; read it and approve the epic again "
        f"by moving it to {APPROVAL_LANE} — that re-runs the review. A plan "
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
    for tag in (POST_UNREAD_TAG, POST_SENT_BACK_TAG):
        if first.startswith(f"🚨 {tag}:"):
            return tag
    return None


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))


# --- The bound --------------------------------------------------------------

def _count_word(n: int) -> str:
    """`two failed rounds`, not `2 failed rounds` — the bound is a sentence a
    non-technical reader meets on their own epic (standards/comms.md). Falls
    back to the digit for any count the words do not cover, so raising
    MAX_ROUNDS can never produce a wrong word."""
    return {1: "one", 2: "two", 3: "three"}.get(n, str(n))


def decide(result: str, prior_send_backs: int, reason: str = "",
           stage: str = STAGE_PRE) -> tuple[str, str]:
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
    if failed < MAX_ROUNDS:
        return "hold", f"sent back — round {failed} of {MAX_ROUNDS}"
    if stage == STAGE_POST:
        note = (
            f"{_count_word(failed)} failed rounds at this critic — the bound. "
            "This plan has been sent back twice since it was approved, so it "
            "parks for you with `needs-human` instead of being built as it "
            "stands. The critic's stated reason, unresolved: "
        ) + one_line(reason)
        return "hold", note
    note = (
        f"{_count_word(failed)} failed rounds at this critic — the bound, so the "
        "plan proceeds to the CEO regardless rather than circling. "
        "The critic's stated reason, unresolved: "
    ) + one_line(reason)
    return "proceed", note


def at_bound(action: str, prior_send_backs: int, result: str) -> bool:
    """Did THIS decision spend the last round of the budget? True only for a
    real send-back that holds at MAX_ROUNDS — the post stage's park signal.
    A crash or a pass never reaches the bound, whatever the count says."""
    return (action == "hold" and result == SEND_BACK
            and prior_send_backs + 1 >= MAX_ROUNDS)


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
# There is no bound here either, because there is no loop to bound: one call per
# one-off classification, and the card either moves or parks.

#: The two actions the one-off exit can take. `proceed` runs
#: `planning_route.py exit`; `escalate` runs `planning_escalation.py escalate`.
PROCEED = "proceed"
ESCALATE = "escalate"

#: What the run records when the critic produced nothing usable. Its own
#: sentence rather than the epic route's, because here it is not a shrug.
NO_CRITIC_NOTE = (
    "the critic produced no result, and on this route that is not a pass — "
    "nothing else reads this card before it is built, so it goes to a person"
)


def one_off_decide(result: str, reason: str = "") -> tuple[str, str]:
    """`(action, note)` for a one-off exit — `proceed` moves it, `escalate` parks it.

    Only a PASS moves the card. A SEND_BACK carries the critic's own line; a
    crash, an empty file, an unparseable header or a verdict this module does
    not write all land on the same answer, because reading any of them as a
    pass is the one direction that must never happen.
    """
    if result == PASS:
        return PROCEED, (
            "the critic read this card and found one pull request of work an "
            "agent can build unattended"
        )
    if result == SEND_BACK and one_line(reason):
        return ESCALATE, one_line(reason)
    return ESCALATE, NO_CRITIC_NOTE


def one_off_escalation(result: str, reason: str = "") -> str:
    """The plain-English question the CEO is handed when a one-off does not pass.

    `standards/comms.md`: purpose first, the finding in its own block, and one
    ask as the closing line. It is written for a non-technical reader because
    it is the CEO's decision queue this lands in, and the reason half of it was
    written by an AGENT — so the same seam that guards the planner's own
    escalation text guards this one (`planning_escalation.jargon`). A reason
    that leaks a path or a command costs the REASON, never the question: the
    raw text stays in the run log, and the card still parks with something a
    person can answer.
    """
    import planning_escalation  # late: it reads planning_route, which reads us

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
_CHECK_ITEM = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*\S", re.MULTILINE)

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
    """
    return plan_footprint.collisions(cards)


def mechanical_findings(cards: list[dict], plan_comment: str = "",
                        surfaces: list[str] | None = None) -> list[str]:
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
    return findings


def findings_note(cards: list[dict], findings: list[str]) -> str:
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


def _write_outputs(path: str | None, pairs: list[tuple[str, str]]) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in pairs:
                f.write(f"{key}={one_line(value)}\n")
    except OSError as exc:
        print(f"plan critic: could not write step outputs: {exc}")


def _cmd_charter(args) -> int:
    print(charter(args.stage, sight=_read(args.sight_file)), end="")
    return 0


def _cmd_mechanical(args) -> int:
    cards = _stdin_json([])
    surfaces = []
    if args.surfaces_dir and os.path.isdir(args.surfaces_dir):
        for root, _dirs, files in os.walk(args.surfaces_dir):
            surfaces += [os.path.join(root, f) for f in files
                         if f.lower().endswith(".png")]
    findings = mechanical_findings(cards, _read(args.plan_comment_file), sorted(surfaces))
    if args.note_file:
        # The comment the rail posts to the epic before the critic reads it.
        with open(args.note_file, "w", encoding="utf-8") as f:
            f.write(findings_note(cards, findings) + "\n")
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
    result, reason = read_result(_read(args.result_file))
    collisions = collisions_declared(_read(args.result_file))
    # The budget, and the round number, belong to THIS planning attempt. A
    # re-planned epic counted against the whole thread would spend a budget it
    # never used, and say "round 3 of 2" while doing it.
    cycle = current_cycle(thread, args.epic)
    prior = send_backs(cycle, args.stage)
    if args.stage == STAGE_ONE_OFF:
        # No bound and no loop on this route: one call, and the card either
        # moves or parks. `prior` is still read so a re-run of the same card
        # numbers its round honestly.
        action, note = one_off_decide(result, reason)
    else:
        action, note = decide(result, prior, reason, stage=args.stage)
    stats = rate(cycle, args.stage)
    # The round NUMBER counts every round this stage has run, including ones it
    # passed or crashed on; the BOUND counts only the failed ones. Two different
    # questions, and conflating them would spend the budget on a crash.
    round_n = stats["rounds"] + 1
    # `bound` is the workflow's park signal (DRE-3088): a post-stage hold at
    # the last round parks the epic with `needs-human` instead of asking the
    # CEO to approve the same plan a third time.
    bound = at_bound(action, prior, result)

    _write_outputs(args.github_output, [
        ("action", action),
        ("result", result),
        ("reason", reason),
        ("round", str(round_n)),
        ("note", note),
        ("collisions", str(collisions)),
        ("bound", "true" if bound else "false"),
    ])

    title = STAGES[args.stage]["title"]
    icon = {"hold": "🛑", "proceed": "✅", ESCALATE: "🙋"}[action] \
        if result != NO_RESULT else "⚠️"
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
    # The one-off route has no round bound, so its note does not claim one:
    # "round 1 of 2" on a card that will never get a second call is a number
    # that means nothing to the person reading it.
    if args.stage == STAGE_ONE_OFF:
        headline = f"{icon} **{title}** — {note}"
        # ...and what happens next, in the words of the route it is on. The
        # send-back RATE belongs to a planning attempt and this card has none.
        closing = (
            "This card goes to the build queue."
            if action == PROCEED
            else "This card is not going to the build queue — it is with a "
                 "person, in the decision queue, with the reason above."
        )
    else:
        headline = f"{icon} **{title}** — round {round_n} of {MAX_ROUNDS}: {note}"
        closing = rate_text
    body = "\n\n".join([
        headline,
        *( [f"Reason: {reason}"] if reason and reason != note else [] ),
        closing,
    ])
    record = marker(args.stage, round_n, result, reason, collisions)
    if args.note_file:
        with open(args.note_file, "w", encoding="utf-8") as f:
            f.write(body + "\n")
    if args.record_file:
        with open(args.record_file, "w", encoding="utf-8") as f:
            f.write(record + "\n")
    # Only when the card does NOT pass, and only on the route that has an
    # escalation exit: a reason file left behind by a passing card is a
    # question nobody owes an answer to, and the step that reads it is gated on
    # the action rather than on the file existing.
    if args.escalation_file and args.stage == STAGE_ONE_OFF and action == ESCALATE:
        with open(args.escalation_file, "w", encoding="utf-8") as f:
            f.write(one_off_escalation(result, reason) + "\n")
    print(body)
    print()
    print(record)
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


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("charter", help="print a stage's prompt block")
    c.add_argument("stage", choices=sorted(STAGES))
    c.add_argument("--sight-file", default=None)
    c.set_defaults(fn=_cmd_charter)

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

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
