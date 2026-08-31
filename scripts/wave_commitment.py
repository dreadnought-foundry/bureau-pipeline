#!/usr/bin/env python3
"""Progressive commitment — an epic inside an approved wave gets its own green
light when its turn comes (DRE-2846).

Approving a wave approves **the shape and the order, and nothing else.** Before
this module nothing could tell *approved as part of a wave* from *approved to
build*: there was no field to read, so a wave approval was a blank cheque over
every epic under it. Wave 1.5 is the live argument — approved 2026-08-23 as a
shape, and by 2026-08-29 two of its cards had been rewritten, one had been split
into four, and its phase count had gone from seven to nine. Every one of those
changes was right, and a single approval covering all of them would have been an
approval of something nobody had read.

## The state, and it is new

`committed-in-sequence` is recorded on the epic itself, as one machine-readable
comment — the `routing_verdict.py` / `planning_shape.py` grammar the sweep
already counts occurrences of, not a second one. It says exactly one thing: this
epic is part of a wave whose shape and order were approved, and *nothing more
about it has been*.

Its counterpart, `green-lit`, is **observed and never stamped**: the epic
entered an active lane on its own account, which is what an epic's approval is
everywhere else in this pipeline (`mid_epic.EPIC_ACTIVE_LANES`). Deriving it
from Linear's own history rather than writing a second marker is
`standards/console-honesty.md` rule 1 — a stamp could say approved while the
board said otherwise, and the board is the truth.

## The turn, and why it is not the CEO's queue

When an epic's turn comes it goes to the lane **no epic leaves without a plan
artifact**, and that lane is DERIVED from `config/lane-contract.json` rather
than typed here. That is the whole of the third acceptance criterion: routing
the turn straight into the decision lane would hand the CEO the artifact written
when the wave was approved, and by then the world has moved. Passing through the
artifact lane means the document the CEO reads is written at that moment, by the
existing epic route, which this module does not re-implement.

## Changing the sequence costs nothing

Reordering or dropping an epic inside an approved wave does not re-open the
wave's approval — the wave's shape is a commitment to a set and an order, and
both were always going to move. What it does owe is a RECORD: every change is
written, with its reason and its date, into the managed region of the wave's own
description, which is where the CEO reads the plan (the same place
`mid_epic.py` records an epic's growth, for the same reason). The order is
judged by the wave plan's OWN dependency rule — `wave_plan.epic_defects`, the
function, not a copy of it — so a reorder that would put an epic before
something it depends on is refused rather than recorded.

CLI:

    python3 scripts/wave_commitment.py record <WAVE> --plan wave-plan.md
    python3 scripts/wave_commitment.py commit <WAVE> [--if-approved]
    python3 scripts/wave_commitment.py advance <WAVE>
    python3 scripts/wave_commitment.py reorder <WAVE> <key> (--after <key>|--first) \\
        --because "<one line>"
    python3 scripts/wave_commitment.py drop <WAVE> <key> --because "<one line>"
    python3 scripts/wave_commitment.py state <CARD>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402
import mid_epic  # noqa: E402
import plan_artifact  # noqa: E402
import planning_shape  # noqa: E402
import wave_plan  # noqa: E402

# The machine-readable record, in the grammar `routing_verdict.VERDICT_TAG` and
# `planning_shape.SHAPE_TAG` already use — reuse the pattern, do not invent one.
COMMITMENT_TAG = "wave-commitment"
COMMITMENT_MARK = "🌊"

# The two states this module records, and the one it OBSERVES. They are
# different answers to different questions, which is the entire point: nothing
# before this could tell them apart.
COMMITTED = "committed-in-sequence"   # recorded: the wave's shape covers it
DROPPED = "dropped"                   # recorded: it is no longer in the wave
GREEN_LIT = "green-lit"               # observed: it was approved on its own

# The sweep's refusal receipt. Deliberately NOT the record's own marker: the
# notice NAMES the state it is refusing, and a reader matching the marker
# anywhere in a body would read the refusal back as the record.
NOT_GREEN_LIT_TAG = "wave-not-green-lit"

# The managed region in the WAVE's description — the place the CEO reads the
# plan. Fenced by HTML comments so it is invisible in rendered Linear and
# unambiguous to parse, exactly as `mid_epic.ARTIFACT_BEGIN` is.
REGION_BEGIN = "<!-- BEGIN wave-commitment (managed by scripts/wave_commitment.py) -->"
REGION_END = "<!-- END wave-commitment -->"

# The machine truth inside that region: one fenced block, read back with the
# artifact's own fence scanner. Prose is rendered FROM it, so the two cannot
# disagree.
LEDGER_FENCE = "commitment"

# The shape whose destination is where an epic's own green light is given. Read
# from `config/planning-shapes.json`, never spelled here.
EPIC_SHAPE = "epic"

# What "its predecessor is finished" means. The sweep's own terminal set.
SETTLED_STATES = ("Done", "Canceled", "Duplicate")

# The clause that identifies the lane an epic's turn sends it to: the live lane
# whose EXIT an epic cannot make without a plan artifact.
_ARTIFACT_CLAUSE = "plan artifact"

# The marker must OPEN the comment (anchored, `re.match`). This module's own
# notices quote the state they are talking about.
_RECORD_LINE = re.compile(
    rf"\s*{COMMITMENT_MARK}\s*{COMMITMENT_TAG}:\s*\**([a-z][a-z-]*[a-z])\b"
)
_CARD_REF = re.compile(r"\b([A-Z]{2,6}-\d+)\b")

_REGION = re.compile(
    re.escape(REGION_BEGIN) + r".*?" + re.escape(REGION_END), re.DOTALL
)


class CommitmentError(RuntimeError):
    """The commitment cannot be read — a wave with no ledger, a lane contract
    that no longer names the lane a turn goes to. Raised rather than defaulted:
    guessing here either dispatches an epic nobody approved or freezes a wave
    with nothing saying why."""


class CommitmentRefused(Exception):
    """A change to the sequence that must not be recorded.

    Pre-write on purpose, the `mid_epic.DiscoveryRefused` shape: a drop written
    and then questioned is already a drop, and the epic it stranded is already
    waiting on something that will never come.
    """


# --------------------------------------------------------------------------- #
# the lanes, derived                                                           #
# --------------------------------------------------------------------------- #


def turn_lane(contract: dict | None = None) -> str:
    """The lane an epic's turn sends it to: the live lane whose EXIT an epic
    cannot make without a plan artifact.

    Derived, never typed. The claim this card makes is that an epic reaching its
    turn carries the artifact written AT THAT MOMENT — so the lane and the
    clause that makes the artifact compulsory have to be the same fact. A lane
    name written here would be a second one, and the copy is what drifts.
    """
    hits = [
        lane_doc["name"]
        for lane_doc in lane_contract.lanes("live", contract)
        if _ARTIFACT_CLAUSE in ((lane_doc["clauses"].get("exit") or {}).get("text") or "")
    ]
    if len(hits) != 1:
        raise CommitmentError(
            "config/lane-contract.json names "
            + (f"{len(hits)} lanes" if hits else "no lane")
            + f" whose exit requires a {_ARTIFACT_CLAUSE}, so there is no one "
            "lane an epic's turn can send it to. An epic reaching its turn owes "
            "its own plan artifact; without that clause nothing makes it write "
            "one."
        )
    return hits[0]


def _epic_mark(doc: dict | None = None) -> str:
    """The label that makes a card an epic, from the shape vocabulary's own
    marks. Read rather than typed: the same reuse `planning_shape` makes of
    `agent:planner` in the first place, so renaming it there renames it here."""
    marks = planning_shape.marks(EPIC_SHAPE, doc)
    if not marks:
        raise CommitmentError(
            f"the {EPIC_SHAPE!r} shape in config/planning-shapes.json applies no "
            "marks, so nothing would make a committed epic an epic — the sweep "
            "would promote it as work"
        )
    return marks[0]


def decision_lane(doc: dict | None = None) -> str:
    """Where an epic's own green light is given — the `epic` shape's own
    destination in `config/planning-shapes.json`. The turn never goes here
    directly: that would hand the CEO the artifact the wave was approved on."""
    return planning_shape.destination(EPIC_SHAPE, doc)


# --------------------------------------------------------------------------- #
# the record on the epic                                                       #
# --------------------------------------------------------------------------- #


def commitment_comment(wave: str, entry: dict, position: int, total: int) -> str:
    """The comment that IS the record. One per epic, written when the wave is
    committed."""
    waits = entry.get("depends_on") or []
    after = (
        " after " + ", ".join(f"`{k}`" for k in waits)
        if waits
        else " — nothing is ahead of it"
    )
    return (
        f"{COMMITMENT_MARK} {COMMITMENT_TAG}: **{COMMITTED}** to {wave} — "
        f"{position} of {total}{after}.\n\n"
        f"**What the wave's approval covered:** the shape and the order, and "
        f"nothing else about this epic. This is not an approval to build.\n\n"
        f"**What happens when its turn comes:** this epic moves to "
        f"`{turn_lane()}`, writes its OWN plan artifact then — not the one that "
        f"existed when the wave was approved — and reaches the CEO in "
        f"`{decision_lane()}` on that artifact. Until it does, the sweep will "
        f"not promote it."
    )


def drop_comment(wave: str, entry: dict, because: str) -> str:
    """The record that an epic has left the wave's sequence. It never takes a
    turn after this, and nothing re-approves the wave to say so."""
    return (
        f"{COMMITMENT_MARK} {COMMITMENT_TAG}: **{DROPPED}** from {wave} — "
        f"{_one_line(because)}\n\n"
        "The wave's approval covered a shape and an order, so changing them "
        "does not re-open it. The change is recorded on the wave, where the "
        "plan is read."
    )


def _stamped(comment_bodies) -> list:
    """Every state recorded on this card, oldest first."""
    found = []
    for body in comment_bodies or ():
        match = _RECORD_LINE.match(body or "")
        if match:
            found.append(match.group(1).lower())
    return found


def state(comment_bodies, green_lit_at: str | None = None):
    """This card's wave state, or None when it is not in a wave.

    `green_lit_at` is the epic's own most recent entry into an active lane
    (`mid_epic.last_green_light`). It is a parameter rather than a read because
    the caller pays for it: the sweep buys that history only for a card that
    actually carries the record.
    """
    stamps = _stamped(comment_bodies)
    if not stamps:
        return None
    latest = stamps[-1]  # the newest record wins — a drop supersedes a commit
    if latest == DROPPED:
        return DROPPED
    if latest != COMMITTED:
        return None
    return GREEN_LIT if green_lit_at else COMMITTED


def wave_on(comment_bodies) -> str | None:
    """The wave this card is committed to, read off its own record."""
    for body in comment_bodies or ():
        if not _RECORD_LINE.match(body or ""):
            continue
        found = _CARD_REF.search((body or "").splitlines()[0])
        if found:
            return found.group(1)
    return None


# --------------------------------------------------------------------------- #
# the sweep reads it                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Arrival:
    """Where an epic whose turn has come goes, and what is said on it."""

    lane: str
    note: str


def arrival_note(identifier: str, wave: str | None = None) -> str:
    belongs = f" in {wave}" if wave else ""
    return (
        f"🌊 Its turn has come: {identifier} is the next epic{belongs}, and it "
        f"is moving to `{turn_lane()}`.\n\n"
        f"**What it owes now:** its own plan artifact, written at this moment. "
        f"The wave's approval covered the shape and the order — not this epic — "
        f"so it comes back to `{decision_lane()}` for its own green light, "
        f"carrying the plan as it stands today rather than the one the wave was "
        f"approved on."
    )


def turn_arrival(identifier: str, comment_bodies,
                 green_lit_at: str | None = None) -> Arrival | None:
    """Where the sweep must send this card now, or None when it is not a
    committed-in-sequence epic waiting for its turn.

    An epic that already has its own green light is None: it has been approved,
    and sending it round the planning loop again would undo the CEO's decision.
    """
    if state(comment_bodies, green_lit_at) != COMMITTED:
        return None
    return Arrival(turn_lane(), arrival_note(identifier, wave_on(comment_bodies)))


def promotion_refusal(identifier: str, comment_bodies,
                      green_lit_at: str | None = None) -> str | None:
    """Why the sweep must not promote `identifier`, or None to let it through.

    The whole rule: being inside an approved wave is not being approved to
    build. Read off the card's own RECORD, which is why this holds where
    `agent:planner` does not — that label is a thing a human edits in Linear,
    and one edit must not turn a wave's approval into an approval of everything
    under it.
    """
    now = state(comment_bodies, green_lit_at)
    if now in (None, GREEN_LIT):
        return None
    wave = wave_on(comment_bodies) or "its wave"
    if now == DROPPED:
        return (
            f"🚨 {NOT_GREEN_LIT_TAG}: {identifier} was **{DROPPED}** from "
            f"{wave}, so nothing is coming for it — the sweep is not promoting "
            "it.\n\n"
            "Dropping an epic inside an approved wave needs no re-approval, and "
            "the reason is recorded on the wave where the plan is read. If it "
            "is wanted again, put it back in the wave's sequence rather than "
            "nudging this card."
        )
    return (
        f"🚨 {NOT_GREEN_LIT_TAG}: {identifier} is **{COMMITTED}** to {wave} and "
        "has had no green light of its own — the sweep is not promoting it.\n\n"
        "Approving a wave approves the shape and the order, and nothing else. "
        f"When this epic's turn comes it moves to `{turn_lane()}`, writes its "
        f"own plan artifact then, and reaches the CEO in `{decision_lane()}` on "
        "that artifact — because by then the world has moved, and the epic the "
        "wave sketched in week one may not be the epic worth building in week "
        "five.\n\n"
        "**To let it through:** give it its own green light — the CEO moves the "
        "epic out of the decision lane once its plan is read. Nothing else "
        "does, and nothing on this card can."
    )


def refusal_tag(refusal: str | None) -> str | None:
    """The idempotency tag `refusal` is surfaced under, or None.

    Read OFF the notice rather than inferred by the caller, the
    `routing_verdict.refusal_tag` rule: the sweep posts each refusal at most
    once keyed on the tag, and a notice paired with the wrong tag silences
    another refusal.
    """
    first = ((refusal or "").splitlines() or [""])[0]
    return NOT_GREEN_LIT_TAG if first.startswith(f"🚨 {NOT_GREEN_LIT_TAG}:") else None


# --------------------------------------------------------------------------- #
# the ledger: the wave's commitment, where the CEO reads the plan              #
# --------------------------------------------------------------------------- #


def ledger_from_plan(wave: str, plan_md: str) -> dict:
    """The commitment, read out of the wave plan's own ```epics block.

    `wave_plan.epics` raises on a plan that names none — a wave that commits to
    no epic has not been decomposed, and a silent empty ledger would let it read
    as one that was.
    """
    epics = []
    for record in wave_plan.epics(plan_md):
        if not isinstance(record, dict):
            raise CommitmentRefused(
                "the plan's ```epics block carries something that is not a "
                "record, so the wave commits to nothing readable"
            )
        epics.append({
            "key": record.get("key"),
            "title": record.get("title"),
            "depends_on": list(record.get("depends_on") or []),
            "card": None,
            "status": COMMITTED,
        })
    ledger = {"wave": wave, "epics": epics, "changes": []}
    problems = order_defects(epics)
    if problems:
        raise CommitmentRefused(
            "this plan's epics cannot be committed to as written: "
            + "; ".join(problems)
        )
    return ledger


def order_defects(epics) -> list:
    """Everything wrong with this sequence, judged by the wave plan's OWN rule.

    The FUNCTION, not a copy of it: `wave_plan.epic_defects` is what refuses an
    out-of-order ```epics block in the plan, and the ledger is that same block
    once it has card numbers on it. A second implementation here would be a
    second answer to "is this order legal", and they would diverge on the first
    edit to either.
    """
    live = [
        {"key": e.get("key"), "title": e.get("title"),
         "depends_on": list(e.get("depends_on") or [])}
        for e in epics if e.get("status") != DROPPED
    ]
    return wave_plan.epic_defects(
        "```epics\n" + json.dumps(live) + "\n```"
    )


def entry(ledger: dict, key: str) -> dict | None:
    for record in ledger.get("epics") or []:
        if record.get("key") == key:
            return record
    return None


def _require(ledger: dict, key: str) -> dict:
    found = entry(ledger, key)
    if found is None:
        raise CommitmentRefused(
            f"{key!r} is not an epic this wave committed to — it commits to "
            + ", ".join(repr(e.get("key")) for e in ledger.get("epics") or [])
        )
    return found


def turn(ledger: dict, settled=()) -> tuple:
    """The keys whose turn it is: still committed, not finished, and everything
    they wait for is settled.

    `settled` is what has actually finished (or been dropped) — read from
    Linear by the caller, never assumed from position in the list. The list's
    order is what the CEO approved; what makes a turn arrive is the work.
    """
    done = set(settled or ())
    ready = []
    for record in ledger.get("epics") or []:
        if record.get("status") != COMMITTED:
            continue
        if record.get("key") in done:
            continue
        if all(dep in done for dep in record.get("depends_on") or []):
            ready.append(record["key"])
    return tuple(ready)


def _changed(ledger: dict, what: str, because: str) -> dict:
    changes = list(ledger.get("changes") or [])
    changes.append({
        "at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "what": what,
        "because": _one_line(because),
    })
    return {**ledger, "changes": changes}


def _reason(because: str) -> str:
    if not (because or "").strip():
        raise CommitmentRefused(
            "a change to an approved wave's sequence needs a one-line reason. "
            "The change itself costs nothing — no re-approval — and the reason "
            "is the entire record the CEO reads afterwards. Without it the "
            "sequence simply differs from the one that was approved, and "
            "nothing says why."
        )
    return _one_line(because)


def reorder(ledger: dict, key: str, *, after: str | None = None,
            first: bool = False, because: str = "") -> dict:
    """Move an epic in an approved wave's sequence. No re-approval.

    `--after X` is "it waits for X now"; `--first` is "it waits for nothing".
    Both are refused when the result would put an epic before something it
    depends on — that is the order the CEO approved, and the wave plan's own
    rule is what judges it.
    """
    because = _reason(because)
    moving = _require(ledger, key)
    if moving.get("status") == DROPPED:
        raise CommitmentRefused(
            f"{key!r} was dropped from this wave — put it back in the sequence "
            "before reordering it"
        )
    if not first:
        if not after:
            raise CommitmentRefused(
                "say where it goes: --after <key>, or --first"
            )
        target = _require(ledger, after)
        if after == key:
            raise CommitmentRefused(f"{key!r} cannot be ordered after itself")
        if target.get("status") == DROPPED:
            raise CommitmentRefused(
                f"{after!r} was dropped from this wave, so nothing can wait "
                "for it"
            )

    epics = [dict(e) for e in ledger["epics"]]
    moved = next(e for e in epics if e["key"] == key)
    moved["depends_on"] = [] if first else [after]
    epics.remove(moved)
    where = 0 if first else next(i for i, e in enumerate(epics) if e["key"] == after) + 1
    epics.insert(where, moved)

    problems = order_defects(epics)
    if problems:
        raise CommitmentRefused(
            f"moving {key!r} would break the order this wave was approved on: "
            + "; ".join(problems)
        )
    what = f"reordered `{key}` " + ("to the front" if first else f"after `{after}`")
    return _changed({**ledger, "epics": epics}, what, because)


def drop(ledger: dict, key: str, because: str = "") -> dict:
    """Take an epic out of an approved wave's sequence. No re-approval.

    Refused when another epic still waits for it: dropping it would leave that
    one waiting for something that is never coming, which is the silent version
    of the deadlock this pipeline already pays for elsewhere.
    """
    because = _reason(because)
    _require(ledger, key)
    epics = [dict(e) for e in ledger["epics"]]
    for record in epics:
        if record["key"] == key:
            record["status"] = DROPPED
    problems = order_defects(epics)
    if problems:
        raise CommitmentRefused(
            f"dropping {key!r} would strand the epics that wait for it: "
            + "; ".join(problems)
        )
    return _changed({**ledger, "epics": epics}, f"dropped `{key}`", because)


# --------------------------------------------------------------------------- #
# rendering and reading the managed region                                     #
# --------------------------------------------------------------------------- #


def render_ledger(ledger: dict) -> str:
    """The managed region: the sequence a CEO reads, and the record it is
    rendered FROM. One source, so the prose and the machine record cannot
    disagree."""
    epics = ledger.get("epics") or []
    live = [e for e in epics if e.get("status") != DROPPED]
    lines = [
        REGION_BEGIN,
        "",
        "## The commitment",
        "",
        "Approving this wave approved **the shape and the order, and nothing "
        "else**. Each epic below comes back for its own green light when its "
        "turn comes, carrying the plan artifact written at that moment.",
        "",
        f"**Committed:** {len(live)} epics · **Dropped:** {len(epics) - len(live)}",
        "",
        "The order:",
        "",
    ]
    for position, record in enumerate(epics, 1):
        card = f" — {record['card']}" if record.get("card") else ""
        waits = record.get("depends_on") or []
        # A dropped epic waits for nothing — it is out of the sequence, and
        # saying what it "waits for" would read as a turn that is still coming.
        after = (
            " · waits for " + ", ".join(f"`{k}`" for k in waits)
            if waits and record.get("status") != DROPPED
            else ""
        )
        lines.append(
            f"{position}. `{record.get('key')}` — {record.get('title')}{card} — "
            f"**{record.get('status')}**{after}"
        )
    lines += ["", "Changes since the wave was approved:", ""]
    changes = ledger.get("changes") or []
    if changes:
        for change in changes:
            lines.append(f"- {change['at']} — {change['what']} — {change['because']}")
    else:
        lines.append("- (none)")
    lines += [
        "",
        "The record this section is rendered from:",
        "",
        f"```{LEDGER_FENCE}",
        json.dumps(
            {"wave": ledger.get("wave"), "epics": epics, "changes": changes},
            indent=2,
        ),
        "```",
        "",
        REGION_END,
    ]
    return "\n".join(lines)


def parse_ledger(description: str) -> dict | None:
    """Read the ledger back out of a wave's description, or None when there is
    none. Read from the fenced record, never re-derived from the prose — the
    prose is a rendering, and parsing a rendering is how the two drift."""
    found = _REGION.search(description or "")
    if not found:
        return None
    blocks = plan_artifact.fenced_blocks(found.group(0), LEDGER_FENCE)
    if not blocks:
        raise CommitmentError(
            "this wave's commitment region carries no "
            f"```{LEDGER_FENCE} block, so the sequence cannot be read"
        )
    try:
        data = json.loads(blocks[0])
    except json.JSONDecodeError as e:
        raise CommitmentError(
            f"this wave's ```{LEDGER_FENCE} block is not valid JSON: {e}"
        ) from e
    data.setdefault("epics", [])
    data.setdefault("changes", [])
    return data


def merge_ledger(description: str, block: str) -> str:
    """Splice the region into the wave's description, replacing any previous
    one. The CEO-readable plan above it is never touched — that first prose
    paragraph is the plan summary (`standards/card-quality.md`)."""
    body = description or ""
    if _REGION.search(body):
        return _REGION.sub(lambda _: block, body, count=1)
    return (body.rstrip() + "\n\n" + block + "\n") if body.strip() else block + "\n"


# --------------------------------------------------------------------------- #
# Linear-touching seams                                                        #
# --------------------------------------------------------------------------- #
#
# Every function below takes the `linear_ops` MODULE as its first argument —
# the convention `mid_epic`, `break_glass` and `validate_card._bounce` already
# use, so the pure core above needs no API key and the tests need no network.

_WAVE_QUERY = """query($id: String!) { issue(id: $id) {
     id identifier title description
     state { name }
     labels(first: 50) { nodes { name } }
     children(first: 250) { nodes { identifier title state { name } } }
     history(last: 50) { nodes { createdAt toState { name } } }
   } }"""


def read_wave(linear_ops, wave: str) -> dict:
    """The wave as Linear has it: body, lane, labels, the epics under it and
    the history its own green light is read out of. One read per motion."""
    data = linear_ops.gql(_WAVE_QUERY, {"id": wave})
    issue = (data or {}).get("issue") or {}
    if not issue:
        raise CommitmentError(f"Linear has no issue {wave}")
    return issue


def _ledger_of(issue: dict, wave: str) -> dict:
    ledger = parse_ledger(issue.get("description") or "")
    if ledger is None:
        raise CommitmentRefused(
            f"{wave} carries no recorded commitment yet. The wave plan names "
            "the epics it commits to; record that first:\n"
            "  python3 scripts/wave_commitment.py record <WAVE> --plan <plan.md>"
        )
    return ledger


def record(linear_ops, wave: str, plan_md: str) -> dict:
    """Record what this wave commits to, from the plan it was checked against.

    A sequence that is already COMMITTED — its epics exist — is never replaced.
    A wave is approved on a shape and an order, and a later re-plan silently
    swapping them for different ones would be exactly the unread approval this
    card exists to prevent. The changes list is carried forward either way.
    """
    issue = read_wave(linear_ops, wave)
    existing = parse_ledger(issue.get("description") or "")
    if existing and any(e.get("card") for e in existing.get("epics") or []):
        print(
            f"wave commitment: {wave} is already committed to "
            f"{len(existing['epics'])} epic(s) — the approved sequence is not "
            "replaced. Reorder or drop instead."
        )
        ledger = existing
    else:
        ledger = ledger_from_plan(wave, plan_md)
        if existing:
            ledger["changes"] = list(existing.get("changes") or [])
    _write_ledger(linear_ops, wave, issue, ledger)
    print(
        f"wave commitment: {wave} commits to "
        + ", ".join(f"`{e['key']}`" for e in ledger["epics"])
    )
    return ledger


def _write_ledger(linear_ops, wave: str, issue: dict, ledger: dict) -> None:
    merged = merge_ledger(issue.get("description") or "", render_ledger(ledger))
    if merged != (issue.get("description") or ""):
        linear_ops.set_description(wave, merged)
        issue["description"] = merged


def _epic_body(wave: str, record_: dict, ledger: dict) -> str:
    waits = record_.get("depends_on") or []
    blocked = [entry(ledger, k) for k in waits]
    lines = [
        f"An epic committed in sequence by the wave {wave}.",
        "",
        f"**{record_.get('title')}**",
        "",
        "The wave's approval covered the shape and the order, and nothing else "
        "about this epic. When its turn comes this epic writes its own plan "
        "artifact and goes to the CEO for its own green light.",
        "",
        "## Acceptance criteria",
        "",
        "- [ ] The plan for this epic is written when its turn comes, and the "
        "CEO green-lights it on that plan.",
    ]
    named = [b["card"] for b in blocked if b and b.get("card")]
    if named:
        lines += ["", "**Blocked by:** " + ", ".join(named)]
    return "\n".join(lines)


def commit(linear_ops, wave: str, if_approved: bool = False) -> list:
    """Turn an APPROVED wave's recorded sequence into epics, each committed in
    sequence, and start the one whose turn it is.

    Refuses a wave with no green light of its own: there is no approved shape to
    commit to yet, and creating the epics anyway would be this module inventing
    the decision it exists to protect. `if_approved` makes that a no-op instead
    of a refusal, which is what the plan run wants — the same step runs on the
    dispatch that writes the plan and on the dispatch that approves it.
    """
    issue = read_wave(linear_ops, wave)
    approved = mid_epic.green_light_from((issue.get("history") or {}).get("nodes"))
    if not approved:
        if if_approved:
            print(f"wave commitment: {wave} has no green light of its own yet "
                  "— nothing is committed, which is the point")
            return []
        raise CommitmentRefused(
            f"{wave} has not been approved, so there is no shape to commit to. "
            "A wave's epics are recorded as committed-in-sequence only once the "
            "CEO has approved the wave itself."
        )
    ledger = _ledger_of(issue, wave)
    strip = [
        label for label in linear_ops.parent_inherited_labels(
            [n["name"] for n in (issue.get("labels") or {}).get("nodes", [])])
        if label.startswith("agent:") and label != _epic_mark()
    ]

    created = []
    total = len(ledger["epics"])
    # Crash recovery, and it is the reason this is not a bare create loop: a run
    # that dies between creating a card and writing the ledger would, on the
    # retry, create the same epic twice — and a wave whose sequence names one
    # epic under two card numbers is worse than no sequence at all. So an entry
    # with no card ADOPTS a child of this wave that already carries its title
    # before it creates one. Titles come from the plan the CEO approved and are
    # unique within it (`wave_plan.epic_defects` refuses a duplicate key, and
    # the title is what the CEO reads).
    adoptable = {
        (node.get("title") or ""): node["identifier"]
        for node in (issue.get("children") or {}).get("nodes", [])
    }
    for position, record_ in enumerate(ledger["epics"], 1):
        if record_.get("card") or record_.get("status") != COMMITTED:
            continue
        adopted = adoptable.get(record_["title"])
        if adopted:
            print(f"wave commitment: adopting the existing {adopted} for "
                  f"`{record_['key']}` — a previous run created it and did not "
                  "get to record it")
            record_["card"] = adopted
            _write_ledger(linear_ops, wave, issue, ledger)
            if linear_ops.count_comments(adopted, COMMITMENT_TAG) == 0:
                linear_ops.cmd_comment(
                    adopted, commitment_comment(wave, record_, position, total))
            continue
        blockers = [
            (entry(ledger, dep) or {}).get("card")
            for dep in record_.get("depends_on") or []
        ]
        flags = ["--label", _epic_mark()]
        named = [b for b in blockers if b]
        if named:
            flags += ["--blocked-by", ",".join(named)]
        child = linear_ops.cmd_subissue(
            wave, record_["title"], _epic_body(wave, record_, ledger), *flags)
        record_["card"] = child["identifier"]
        created.append(child["identifier"])
        # ORDER IS LOAD-BEARING, the `mid_epic._add` rule. The ledger is written
        # BEFORE the stamp, so a crash between the two leaves the safe half: a
        # recorded epic that is not yet stamped is still held by `agent:planner`
        # and is picked up by the next run, where an unrecorded one would be
        # created a second time.
        _write_ledger(linear_ops, wave, issue, ledger)
        # The role a child normally inherits is a BUILD role, and this child is
        # an epic. Leaving `agent:engineer` on it would let the relay dispatch a
        # build agent at a container — the blank cheque, one label over.
        for label in strip:
            linear_ops.remove_label(child["identifier"], label)
        linear_ops.cmd_comment(
            child["identifier"],
            commitment_comment(wave, record_, position, total))
    _write_ledger(linear_ops, wave, issue, ledger)
    print(f"wave commitment: {wave} committed {len(created)} epic(s) in sequence")
    advance(linear_ops, wave)
    return created


def advance(linear_ops, wave: str) -> list:
    """Move every epic whose turn has come to the lane that owes a plan
    artifact. Idempotent: only a card still in Backlog is moved."""
    issue = read_wave(linear_ops, wave)
    ledger = _ledger_of(issue, wave)
    lanes = {
        node["identifier"]: (node.get("state") or {}).get("name")
        for node in (issue.get("children") or {}).get("nodes", [])
    }
    settled = {
        record_["key"] for record_ in ledger["epics"]
        if record_.get("status") == DROPPED
        or lanes.get(record_.get("card")) in SETTLED_STATES
    }
    moved = []
    for key in turn(ledger, settled):
        record_ = entry(ledger, key)
        card = record_.get("card")
        if not card or lanes.get(card) != "Backlog":
            continue
        linear_ops.cmd_advance(card, turn_lane(), "Backlog")
        linear_ops.cmd_comment(card, arrival_note(card, wave))
        moved.append(card)
    print(
        f"wave commitment: {wave} — {len(moved)} epic(s) reached their turn"
        + (": " + ", ".join(moved) if moved else "")
    )
    return moved


def apply_change(linear_ops, wave: str, kind: str, key: str, *,
                 because: str = "", after: str | None = None,
                 first: bool = False) -> dict:
    """Reorder or drop an epic inside an approved wave, and record it where the
    CEO reads the plan.

    The wave's own lane is never touched: changing the sequence does not
    re-approve the wave, and a motion that moved it would put a decision back in
    front of the CEO that this card says is not needed.
    """
    issue = read_wave(linear_ops, wave)
    ledger = _ledger_of(issue, wave)
    if kind == "drop":
        updated = drop(ledger, key, because)
    elif kind == "reorder":
        updated = reorder(ledger, key, after=after, first=first, because=because)
    else:
        raise CommitmentRefused(
            f"{kind!r} is not a change to a sequence — 'reorder' or 'drop'")
    _write_ledger(linear_ops, wave, issue, updated)
    change = updated["changes"][-1]
    linear_ops.cmd_comment(
        wave,
        f"{COMMITMENT_MARK} The wave's sequence changed: {change['what']} — "
        f"{change['because']}\n\n"
        "This needs no re-approval. Approving a wave approves the shape and the "
        "order, and both were always going to move — what it owes is a record, "
        "and this is it. The order now stands as the commitment above.",
    )
    if kind == "drop":
        card = (entry(updated, key) or {}).get("card")
        if card:
            linear_ops.cmd_comment(
                card, drop_comment(wave, entry(updated, key), because))
    return updated


def last_green_light(linear_ops, identifier: str) -> str | None:
    """When this card was last approved on its own account, or None.

    `mid_epic.last_green_light` — the function, not a second reader: an epic's
    approval is its entry into an active lane, and there is one answer to that
    question in this pipeline.
    """
    return mid_epic.last_green_light(linear_ops, identifier)


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _one_line(text) -> str:
    """Collapse whitespace. Nothing is dropped — the whole reason survives, on
    one line, because it is written into a line-oriented record."""
    return " ".join((text or "").split())


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise CommitmentError(f"cannot read the wave plan {path}: {e.strerror}") from e


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="record what this wave commits to")
    rec.add_argument("wave")
    rec.add_argument("--plan", required=True)

    com = sub.add_parser("commit", help="create the committed epics")
    com.add_argument("wave")
    com.add_argument("--if-approved", action="store_true",
                     help="a no-op when the wave has no green light of its own")

    adv = sub.add_parser("advance", help="move any epic whose turn has come")
    adv.add_argument("wave")

    reo = sub.add_parser("reorder", help="move an epic in the sequence")
    reo.add_argument("wave")
    reo.add_argument("key")
    reo.add_argument("--after")
    reo.add_argument("--first", action="store_true")
    reo.add_argument("--because", required=True)

    dro = sub.add_parser("drop", help="take an epic out of the sequence")
    dro.add_argument("wave")
    dro.add_argument("key")
    dro.add_argument("--because", required=True)

    st = sub.add_parser("state", help="this card's wave state")
    st.add_argument("identifier")

    args = parser.parse_args(argv)
    import linear_ops

    if args.command == "record":
        record(linear_ops, args.wave, _read(args.plan))
        return 0
    if args.command == "commit":
        commit(linear_ops, args.wave, if_approved=args.if_approved)
        return 0
    if args.command == "advance":
        advance(linear_ops, args.wave)
        return 0
    if args.command in ("reorder", "drop"):
        apply_change(
            linear_ops, args.wave, args.command, args.key,
            because=args.because,
            after=getattr(args, "after", None),
            first=getattr(args, "first", False),
        )
        return 0
    if args.command == "state":
        bodies = linear_ops.comment_bodies(args.identifier)
        now = state(bodies, last_green_light(linear_ops, args.identifier))
        print(now if now else "not in a wave")
        return 0
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CommitmentError, CommitmentRefused) as e:
        # The only abort: explicit, at top level, CLI-only. A refused change is
        # a message for whoever asked for it, never a traceback.
        raise SystemExit(f"wave commitment: {e}")
