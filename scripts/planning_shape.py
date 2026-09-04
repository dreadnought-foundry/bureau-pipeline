#!/usr/bin/env python3
"""The planning shape (DRE-2843) — how is this work structured, and what gate
does it owe.

`config/planning-shapes.json` declares the three shapes, each with the lane a
card of that shape goes to and the actor accountable for it there. This module
is the only reader: a shape is stamped on a card as one machine-readable
comment, exactly one per card, and everything downstream reads it from here.

The structure is deliberately `routing_verdict.py`'s, not a second one. That
file already binds every destination and actor to `config/lane-contract.json`,
so a shape naming a lane that does not exist — or an actor that is not a
permitted writer OF THAT LANE — fails `config_problems()` instead of becoming a
dead end. Both halves were written from live incidents: OPERATOR and WORKBENCH
shipped routing to a turn that never came (DRE-2735), and PARKED named an actor
its own destination lane forbids (DRE-2824).

## Shape is not size

`size:XS` through `size:XL` already exist and mean EFFORT. This axis is how the
work is STRUCTURED and what gate it owes, and the two are independent: a
`size:L` one-off is a large single card that still ships in one pull request
and still needs no green light. Reusing the `size:` prefix would put two
questions behind one word — the naming failure the Plan/Entitlement rule exists
to prevent (DRE-1494). `config_problems()` refuses a shape named with that
prefix and refuses a shape whose marks apply a `size:` label, so the axes stay
apart mechanically rather than by good intentions.

## Who stamped it, and whose stamp wins (DRE-3029)

Every stamp says who made it: `hand` when a person ran the CLI below, `planner`
plus the model id when the planner run classified the card itself. Two reasons,
and neither is decoration. DRE-3016's scorer grades the classifier separately
from the plan, and it cannot do that without knowing which stamps a model wrote
and on which model. And a person's stamp is an OVERRIDE: where a hand stamp and
a planner stamp disagree, the hand one is the card's shape and the pair is not
reported as a conflict. Two stamps from the SAME kind of writer are still
refused with both named — nobody overrode anything there, and picking between
them would be inventing the decision.

A stamp written before DRE-3029 carries no `by:` line at all. It reads as
`hand`, which is what it was: the CLI was the only writer there had ever been.

## One shape, and three ways a card can fail to carry one

Exactly one shape is stamped per card. The three faults are separate on purpose,
because they want different next actions and a message that collapsed them
would tell a reader none of the three:

  * **two shapes** — refused, with BOTH named. Picking between them would be
    inventing the decision rather than reading it.
  * **an unrecognised word** — the card was classified into a vocabulary that
    does not exist. The word is named, and so are the shapes that do exist.
  * **no shape at all** — the card has not been classified yet. Nothing is
    wrong with it; something is simply owed.

CLI:

    python3 scripts/planning_shape.py check          # validate the file
    python3 scripts/planning_shape.py read DRE-N     # the shape on a card
    python3 scripts/planning_shape.py stamp DRE-N <shape> --why "…"

The stamp command is the OVERRIDE, not the normal path: the planner run
classifies a card it finds unstamped (`planning_classify.py`, DRE-3029).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CONFIG_PATH = os.path.join(ROOT, "config", "planning-shapes.json")

# The machine-readable record. Same shape as `routing_verdict.VERDICT_TAG` and
# `mid_epic.VERDICT_TAG`, which the sweep already counts occurrences of — reuse
# the pattern, do not invent one.
SHAPE_TAG = "planning-shape"
SHAPE_MARK = "🧩"

# The three faults, each under its own tag. A caller surfaces a notice at most
# once per card keyed on the tag, so pairing a notice with the wrong tag makes
# two faults silence each other — the tag is read OFF the notice
# (`fault_tag`), never inferred by the caller from context.
TWO_SHAPES_TAG = "planning-two-shapes"
UNKNOWN_SHAPE_TAG = "planning-unknown-shape"
NO_SHAPE_TAG = "planning-no-shape"

# The marker must OPEN the comment: this module's own fault notices NAME the
# shape they are complaining about, and a reader that matched anywhere in a body
# would read a complaint back as the stamp. Case-insensitive and lower-cased on
# read — tolerant on read, strict on write, so a human retyping the marker in
# Linear does not silently produce a card with no shape.
_SHAPE_LINE = re.compile(
    rf"^\s*{SHAPE_MARK}\s*{SHAPE_TAG}:\s*\**([A-Za-z][A-Za-z-]*[A-Za-z])\b"
)

# Who stamped it (DRE-3029). `hand` is a person at the CLI and is the override;
# `planner` is the classification step, which must also name its model.
BY_HAND = "hand"
BY_PLANNER = "planner"
STAMPERS = (BY_HAND, BY_PLANNER)

# Read anywhere in the stamp, not only at its head: the marker line is what
# identifies the comment as a stamp, and this line follows it. A stamp written
# before DRE-3029 has no such line and reads as `hand` — the CLI was the only
# writer that had ever existed.
_BY_LINE = re.compile(
    r"^\*\*Stamped by:\*\*\s*`([a-z-]+)`(?:\s*·\s*\*\*model:\*\*\s*`([^`]+)`)?", re.M
)

# The effort axis, and the one prefix a shape may never wear.
SIZE_PREFIX = "size:"


class ShapeError(RuntimeError):
    """The vocabulary file is malformed, or a shape was written without the
    reason it owes. Raised rather than defaulted: a vocabulary that silently
    loses a field is a vocabulary that silently stops classifying."""


class UnknownShape(Exception):
    """A shape name the vocabulary does not carry.

    Raised rather than guessed, and deliberately NOT the same answer as a card
    with no shape at all. "Classified into a vocabulary that does not exist" and
    "not classified yet" are different faults with different next actions.
    """


class ConflictingShapes(Exception):
    """One card, two shapes. Exactly one is stamped per card, and a reader that
    picked between them would be inventing the decision rather than reading
    it."""


# --------------------------------------------------------------------------- #
# loading                                                                      #
# --------------------------------------------------------------------------- #

_CACHE: dict = {}


def load(path: str | None = None) -> dict:
    """Parse the vocabulary. Cached per path — a reader consulted once per card
    on a sweep must not put a file read inside every card."""
    path = path or CONFIG_PATH
    if path not in _CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                _CACHE[path] = json.load(fh)
        except (OSError, ValueError) as e:
            raise ShapeError(f"cannot read the shape vocabulary at {path}: {e}") from e
    return _CACHE[path]


def _records(doc: dict | None = None) -> tuple:
    """The shape entries of `doc`, or of the shipped file when none is given.

    `None` means "read the file", and nothing else does: a doc that is merely
    empty was still passed in deliberately, and quietly answering from the real
    config instead would answer a question nobody asked.
    """
    doc = doc if doc is not None else load()
    try:
        return tuple(doc["shapes"])
    except (KeyError, TypeError) as e:
        raise ShapeError(f"this vocabulary declares no shapes: {e}") from e


def shapes(doc: dict | None = None) -> tuple:
    """The shape names, in the file's own order."""
    return tuple(entry["name"] for entry in _records(doc))


def record(name: str, doc: dict | None = None) -> dict:
    for candidate in _records(doc):
        if candidate["name"] == name:
            return candidate
    raise UnknownShape(
        f"{name!r} is not a planning shape — the vocabulary carries "
        f"{', '.join(shapes(doc))}"
    )


def means(name: str, doc: dict | None = None) -> str:
    """What this shape says about the work."""
    return record(name, doc)["means"]


def destination(name: str, doc: dict | None = None) -> str:
    """The lane a card of this shape goes to."""
    return record(name, doc)["destination"]


def actor(name: str, doc: dict | None = None) -> str:
    """Who is accountable for the card at that destination. It must be a
    permitted writer OF THAT LANE (DRE-2824), not merely a writer somewhere."""
    return record(name, doc)["actor"]


def is_promotable(name: str, doc: dict | None = None) -> bool:
    """May the sweep promote a card of this shape?"""
    return bool(record(name, doc)["promotable"])


def marks(name: str, doc: dict | None = None) -> tuple:
    """The labels the stamp applies — signals the rest of the pipeline already
    reads, never a new parallel vocabulary, and never the effort axis."""
    return tuple(record(name, doc)["marks"])


def why(name: str, doc: dict | None = None) -> str:
    return record(name, doc)["why"]


# --------------------------------------------------------------------------- #
# the file checks itself                                                       #
# --------------------------------------------------------------------------- #


def config_problems(doc: dict | None = None) -> list:
    """Everything wrong with the vocabulary file, or an empty list.

    This is the half that binds every destination and actor to the lane
    contract instead of to a word somebody liked, and the half that keeps the
    shape axis from quietly becoming the size axis.
    """
    doc = doc if doc is not None else load()
    problems: list[str] = []
    try:
        lanes = set(lane_contract.lane_names(status="live"))
        writers = set(lane_contract.writers())
    except Exception as e:  # noqa: BLE001 — an unreadable contract is a problem, not a crash
        return [f"the lane contract could not be read, so nothing can be bound to it: {e}"]

    if not (doc.get("_readme") or ()):
        problems.append(
            "the file carries no _readme — JSON has no comments, so the next "
            "reader learns why shape and size are separate axes here or nowhere"
        )

    seen: set = set()
    for entry in _records(doc):
        name = entry.get("name") or ""
        if not name.strip():
            problems.append("a shape has no name")
            continue
        if name in seen:
            problems.append(f"the shape {name!r} is declared twice")
        seen.add(name)
        if name != name.lower():
            problems.append(f"the shape {name!r} must be written lower-case")
        if name.lower().startswith("size"):
            problems.append(
                f"the shape {name!r} wears the size prefix — `size:XS` through "
                "`size:XL` mean EFFORT, and shape is a different axis. Two "
                "questions behind one word is the naming failure the "
                "Plan/Entitlement rule exists to prevent (DRE-1494)"
            )

        for key in ("means", "destination", "actor", "why"):
            if not (entry.get(key) or "").strip():
                problems.append(f"shape {name!r} says nothing for {key!r}")

        if entry.get("destination") not in lanes:
            problems.append(
                f"shape {name!r} goes to {entry.get('destination')!r}, which is "
                "not a live lane in config/lane-contract.json — a destination "
                "nothing can reach is a dead end"
            )
        if entry.get("actor") not in writers:
            problems.append(
                f"shape {name!r} names the actor {entry.get('actor')!r}, which is "
                "not in the lane contract's writer glossary — a shape with no "
                "actor is a card nobody is coming for"
            )
        elif entry.get("destination") in lanes:
            # The STRONG half (DRE-2824): being a writer somewhere is not being
            # allowed to write THIS lane.
            permitted = lane_contract.lane_writers(entry["destination"])
            if entry["actor"] not in permitted:
                problems.append(
                    f"shape {name!r} names the actor {entry['actor']!r} on the "
                    f"destination {entry['destination']!r}, which permits only "
                    f"{', '.join(permitted)} — an actor the destination lane does "
                    "not permit cannot legally act on the card it is sent"
                )

        if not isinstance(entry.get("promotable"), bool):
            problems.append(f"shape {name!r} does not say whether it is promotable")

        applied = entry.get("marks")
        if not isinstance(applied, list):
            problems.append(f"shape {name!r} does not state its marks as a list")
            continue
        for label in applied:
            if not isinstance(label, str) or not label.strip():
                problems.append(f"shape {name!r} applies an empty mark")
                continue
            if label != label.lower():
                problems.append(f"shape {name!r} applies the mark {label!r} un-lower-cased")
            if label.lower().startswith(SIZE_PREFIX):
                problems.append(
                    f"shape {name!r} applies the mark {label!r} — the size axis is "
                    "EFFORT and a shape stamp must never write it. A `size:L` "
                    "one-off is legitimate, and stays the size somebody chose"
                )

    promotable = [entry["name"] for entry in _records(doc) if entry.get("promotable")]
    if len(promotable) != 1:
        problems.append(
            "exactly one shape may be promoted by the sweep; "
            f"{promotable or 'none'} are — the shapes a planner owns are moved by "
            "the people who approve them"
        )
    return problems


# --------------------------------------------------------------------------- #
# reading a card                                                               #
# --------------------------------------------------------------------------- #


def shape_comment(
    name: str,
    reason: str,
    doc: dict | None = None,
    *,
    by: str = BY_HAND,
    model: str | None = None,
) -> str:
    """The comment that IS the shape. One card, one of these.

    `by` says who made the call and `model` which model made it — a stamp by a
    model that does not name the model is a stamp DRE-3016's scorer cannot
    grade, so it is refused rather than written without one.
    """
    entry = record(name, doc)
    if not (reason or "").strip():
        raise ShapeError(
            f"a {name} stamp must say why. A classification with no reason "
            "cannot be argued with, and the next reader inherits a word with "
            "nothing behind it."
        )
    if by not in STAMPERS:
        raise ShapeError(
            f"{by!r} is not a stamper — a stamp says who made the call, and the "
            f"writers are {', '.join(STAMPERS)}"
        )
    if by != BY_HAND and not (model or "").strip():
        raise ShapeError(
            f"a stamp written by {by!r} must name the model it ran on. A "
            "classification nobody can attribute to a model is one nothing can "
            "score separately from the plan (DRE-3016)."
        )
    stamped_by = f"**Stamped by:** `{by}`"
    if (model or "").strip():
        stamped_by += f" · **model:** `{model.strip()}`"
    lines = [
        f"{SHAPE_MARK} {SHAPE_TAG}: **{name}** — {entry['means']}",
        "",
        f"**Why:** {reason.strip()}",
        "",
        stamped_by,
        "",
        f"**Where it goes:** {entry['destination']}. "
        f"**Who handles it there:** {entry['actor']}.",
    ]
    if entry["marks"]:
        lines.append(
            "**Marked:** " + ", ".join(f"`{m}`" for m in entry["marks"]) + "."
        )
    if not entry["promotable"]:
        lines.append(
            "The sweep does not promote a card of this shape — it is moved by "
            "whoever approves it."
        )
    return "\n".join(lines)


def _stamped(comment_bodies) -> list:
    """Every shape stamped on the card as `(name, by)`, lower-cased, in the
    order first seen. Unfiltered — recognising them is the caller's next step.

    A stamp with no `**Stamped by:**` line predates DRE-3029 and reads as
    `hand`: the CLI was the only writer there had ever been.
    """
    seen: list[tuple] = []
    for body in comment_bodies or ():
        text = (body or "").lstrip()
        match = _SHAPE_LINE.match(text)
        if not match:
            continue
        name = match.group(1).strip().lower()
        if any(name == found for found, _ in seen):
            continue
        who = _BY_LINE.search(text)
        seen.append((name, (who.group(1) if who else BY_HAND)))
    return seen


def stamped_by(comment_bodies) -> tuple:
    """`(who, model)` for the FIRST stamp on the card, or `(None, None)`.

    Read by DRE-3016's scorer, which grades the classifier separately from the
    plan and so must be able to tell a model's call from a person's.
    """
    for body in comment_bodies or ():
        text = (body or "").lstrip()
        if not _SHAPE_LINE.match(text):
            continue
        who = _BY_LINE.search(text)
        if who is None:
            return (BY_HAND, None)
        return (who.group(1), who.group(2))
    return (None, None)


def shapes_on(comment_bodies, doc: dict | None = None) -> tuple:
    """Every DISTINCT recognised shape stamped on a card, in the order first
    seen. The marker must OPEN a comment: a body that merely quotes a shape —
    a fault notice does exactly that — carries none.

    Where a HAND stamp and a machine stamp disagree, the hand one is the answer
    (DRE-3029): a person overriding the classifier is the override working, not
    a card with two shapes on it. Two stamps by the same kind of writer are
    still both returned, and refused upstream — nobody overrode anything there.
    """
    known = shapes(doc)
    found = [(name, by) for name, by in _stamped(comment_bodies) if name in known]
    names = tuple(name for name, _ in found)
    if len(names) > 1:
        overrides = tuple(name for name, by in found if by == BY_HAND)
        if len(overrides) == 1:
            return overrides
    return names


def unrecognised_on(comment_bodies, doc: dict | None = None) -> tuple:
    """Every DISTINCT stamped word the vocabulary does not carry.

    A separate question from `shapes_on`, and the reason a card classified into
    a vocabulary that does not exist is never reported as an unclassified card.
    """
    known = shapes(doc)
    return tuple(name for name, _ in _stamped(comment_bodies) if name not in known)


def shape_on(comment_bodies, doc: dict | None = None) -> str | None:
    """The card's single shape, or None when nothing has classified it.

    Two recognised shapes raise `ConflictingShapes` naming both. A stamp the
    vocabulary does not carry raises `UnknownShape` naming the word — unless a
    real shape is also stamped, in which case that IS the decision and the
    unknown word is noise `fault()` still reports.
    """
    found = shapes_on(comment_bodies, doc)
    if len(found) > 1:
        raise ConflictingShapes(
            "this card carries " + " and ".join(found) + " — exactly one shape "
            "is stamped per card, and picking between two would be inventing "
            "the decision rather than reading it"
        )
    if found:
        return found[0]
    strange = unrecognised_on(comment_bodies, doc)
    if strange:
        raise UnknownShape(
            "this card is stamped " + " and ".join(repr(s) for s in strange)
            + f", which the vocabulary does not carry — the shapes are "
            f"{', '.join(shapes(doc))}"
        )
    return None


def fault(identifier: str, comment_bodies, doc: dict | None = None) -> str | None:
    """Why this card's shape cannot be read, or None when it reads cleanly.

    Three faults, three tags, three messages — they want different next actions,
    and a notice that collapsed them would tell a reader none of the three.
    """
    found = shapes_on(comment_bodies, doc)
    strange = unrecognised_on(comment_bodies, doc)

    if len(found) > 1:
        return (
            f"🚨 {TWO_SHAPES_TAG}: {identifier} carries "
            + " and ".join(f"**{name}**" for name in found)
            + ". Exactly one shape is stamped per card — picking between two "
            "would be inventing the decision rather than reading it.\n\n"
            "If the classification changed, say so on the card and let a human "
            "retire the one that no longer holds."
        )

    if strange:
        return (
            f"🚨 {UNKNOWN_SHAPE_TAG}: {identifier} is stamped "
            + " and ".join(f"`{name}`" for name in strange)
            + ", which is not a planning shape. The shapes are "
            + ", ".join(f"**{name}**" for name in shapes(doc))
            + ".\n\nThis is not an unclassified card — it is a card classified "
            "into a vocabulary that does not exist, and the word above is where "
            "that happened."
        )

    if not found:
        return (
            f"🚨 {NO_SHAPE_TAG}: {identifier} carries no planning shape, so "
            "nothing can say what gate it owes — a plan artifact and a green "
            "light, or neither.\n\n"
            "**Nothing is waiting for a person here.** The planner run "
            "classifies a card it finds unstamped, and parks it in the CEO's "
            "decision queue when it cannot (DRE-3029). This notice says the "
            "classification has not happened *yet*.\n\n"
            "**To override it by hand:** `python3 scripts/planning_shape.py "
            f'stamp {identifier} <shape> --why "<one line>"`, where `<shape>` '
            "is one of " + ", ".join(f"**{name}**" for name in shapes(doc))
            + ". A hand stamp is an override and it wins."
        )
    return None


def fault_tag(notice: str | None) -> str | None:
    """The idempotency tag `notice` was surfaced under, or None if it is not one
    of this module's faults.

    Every fault OPENS with its own tag, so the tag is read off the notice rather
    than inferred by the caller from context. That matters because a caller
    posts each fault at most once, keyed on the tag: pair a notice with the
    wrong tag and two faults silence each other.
    """
    first = ((notice or "").splitlines() or [""])[0]
    for tag in (TWO_SHAPES_TAG, UNKNOWN_SHAPE_TAG, NO_SHAPE_TAG):
        if first.startswith(f"🚨 {tag}:"):
            return tag
    return None


def stamp_refusal(name: str, comment_bodies, doc: dict | None = None) -> str | None:
    """Why this shape must not be written, or None to write it.

    Pre-write on purpose: a second shape posted and then questioned is already
    on the card, and the card already has two.
    """
    record(name, doc)  # raises UnknownShape
    found = shapes_on(comment_bodies, doc)
    if not found:
        return None
    if found == (name,):
        return f"this card is already stamped {name} — nothing to add"
    return (
        f"this card is already stamped {' and '.join(found)}; refusing to add "
        f"{name}. Exactly one shape is stamped per card. If the classification "
        "changed, say so on the card and let a human retire the old one."
    )


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def stamp(
    lops,
    identifier: str,
    name: str,
    reason: str,
    *,
    by: str = BY_HAND,
    model: str | None = None,
    doc: dict | None = None,
) -> str | None:
    """Write the shape onto the card. Returns the refusal, or None when written.

    One seam for both writers — the CLI below and the classification step
    (`planning_classify.py`, DRE-3029) — so the pre-write refusal, the comment
    and the marks cannot come apart between them.
    """
    refusal = stamp_refusal(name, lops.comment_bodies(identifier), doc)
    if refusal is not None:
        return refusal
    lops.cmd_comment(identifier, shape_comment(name, reason, doc, by=by, model=model))
    for label in marks(name, doc):
        lops.add_label(identifier, label)
    return None


def _cmd_stamp(identifier: str, name: str, reason: str) -> int:
    import linear_ops

    refusal = stamp(linear_ops, identifier, name, reason, by=BY_HAND)
    if refusal is not None:
        print(f"refusing to stamp {identifier}: {refusal}", file=sys.stderr)
        return 1
    print(
        f"stamped {identifier} {name} → {destination(name)} "
        f"(handled there by {actor(name)})"
    )
    return 0


def _cmd_read(identifier: str) -> int:
    import linear_ops

    bodies = linear_ops.comment_bodies(identifier)
    notice = fault(identifier, bodies)
    try:
        name = shape_on(bodies)
    except (ConflictingShapes, UnknownShape):
        name = None

    if name is None:
        # Every branch `shape_on` declines on has a fault, so there is always a
        # notice to print here.
        print(notice, file=sys.stderr)
        return 1

    # A recognised stamp IS the decision (`shape_on`), so a notice alongside one
    # is the noise of a stray unrecognised stamp: worth saying, never worth
    # losing the card's real shape over.
    if notice is not None:
        print(notice, file=sys.stderr)

    print(json.dumps({
        "shape": name,
        "means": means(name),
        "destination": destination(name),
        "actor": actor(name),
        "promotable": is_promotable(name),
        "marks": list(marks(name)),
    }, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")

    read = sub.add_parser("read")
    read.add_argument("identifier")

    stamp = sub.add_parser("stamp")
    stamp.add_argument("identifier")
    stamp.add_argument("shape")
    stamp.add_argument("--why", required=True)

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "check":
        problems = config_problems()
        for problem in problems:
            print(f"  [FAIL] {problem}")
        print(
            f"{len(shapes())} shape(s) checked against the lane contract, "
            f"{len(problems)} problem(s)"
        )
        return 1 if problems else 0

    if command == "read":
        return _cmd_read(args.identifier)

    if command == "stamp":
        return _cmd_stamp(args.identifier, args.shape, args.why)

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
