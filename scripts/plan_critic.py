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
         [--note-file F] [--record-file F]
                                     comment thread (JSON array) on stdin,
                                     from `dump-comments --with-authors`.
                                     The note and the record are TWO comments.
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
}


def question(stage: str) -> str:
    """The one question this stage asks. KeyError on an unknown stage — a typo
    must fail loudly rather than silently produce a critic with no charter."""
    return STAGES[stage]["question"]


def charter(stage: str, sight: str = "") -> str:
    """The stage's prompt block, as the workflow interpolates it.

    `sight` is the cross-epic scope block and reaches the POST stage only: the
    pre stage's charter states it has no cross-epic sight, and handing it one
    would be the same critic twice.
    """
    spec = STAGES[stage]
    if stage == STAGE_PRE:
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
    """
    for raw in (text or "").splitlines():
        m = _RESULT_LINE.match(raw.strip())
        if not m:
            continue
        result = m.group("result")
        reason = one_line(m.group("reason") or "")
        if result == PASS:
            return PASS, ""
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
    rf"^{re.escape(MARKER_PREFIX)}\s+stage=(?P<stage>\w+)\s+round=(?P<round>\d+)\s+"
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


# --- The bound --------------------------------------------------------------

def _count_word(n: int) -> str:
    """`two failed rounds`, not `2 failed rounds` — the bound is a sentence a
    non-technical reader meets on their own epic (standards/comms.md). Falls
    back to the digit for any count the words do not cover, so raising
    MAX_ROUNDS can never produce a wrong word."""
    return {1: "one", 2: "two", 3: "three"}.get(n, str(n))


def decide(result: str, prior_send_backs: int, reason: str = "") -> tuple[str, str]:
    """`(action, note)` — `hold` stops the plan here, `proceed` moves it on.

    The bound: the FIRST send-back holds; the second means two failed rounds,
    and the plan reaches the CEO regardless with the critic's stated reason
    attached. Nothing circles a third time.
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
    note = (
        f"{_count_word(failed)} failed rounds at this critic — the bound, so the "
        "plan proceeds to the CEO regardless rather than circling. "
        "The critic's stated reason, unresolved: "
    ) + one_line(reason)
    return "proceed", note


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
    action, note = decide(result, prior, reason)
    stats = rate(cycle, args.stage)
    # The round NUMBER counts every round this stage has run, including ones it
    # passed or crashed on; the BOUND counts only the failed ones. Two different
    # questions, and conflating them would spend the budget on a crash.
    round_n = stats["rounds"] + 1

    _write_outputs(args.github_output, [
        ("action", action),
        ("result", result),
        ("reason", reason),
        ("round", str(round_n)),
        ("note", note),
        ("collisions", str(collisions)),
    ])

    title = STAGES[args.stage]["title"]
    icon = {"hold": "🛑", "proceed": "✅"}[action] if result != NO_RESULT else "⚠️"
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
    body = "\n\n".join([
        f"{icon} **{title}** — round {round_n} of {MAX_ROUNDS}: {note}",
        *( [f"Reason: {reason}"] if reason else [] ),
        rate_text,
    ])
    record = marker(args.stage, round_n, result, reason, collisions)
    if args.note_file:
        with open(args.note_file, "w", encoding="utf-8") as f:
            f.write(body + "\n")
    if args.record_file:
        with open(args.record_file, "w", encoding="utf-8") as f:
            f.write(record + "\n")
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
