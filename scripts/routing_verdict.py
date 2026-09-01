#!/usr/bin/env python3
"""The routing verdict (DRE-2724) — who builds this card, and how.

`config/routing-verdicts.json` declares the five routes, each with the lane it
sends a card to and the actor who picks it up there. This module is the only
reader: the sweep (`reconcile.py`) refuses to promote a card whose verdict
names a destination that is not Todo-by-an-agent and never reports a PARKED
card as stalled, `docs/routing-verdicts.md` is RENDERED from the same file, and
the write path below is the one way a verdict gets onto a card.

## Why routing and not a score

A quality score has a direction a critic can drift in: mark things good and the
critic looks useful. Routing has no good or bad — only a wrong destination, and
a wrong destination shows up the moment nothing picks the card up.

## The rule, and it is mechanical

Route on whether an unattended agent can SATISFY the acceptance criteria — not
on whether it could write the code. That reads the card's own stated exit
condition instead of guessing from the title, which makes it cheap and hard to
argue with.

Precedence is strict, so the critic is not paid to rediscover what the card
already says:

  1. an explicit role label (`agent:ops`, `no-code`) — no model is asked;
  2. the title convention, ANCHORED at the start — never a substring search;
  3. the acceptance-criteria rule;
  4. only what survives all three reaches a judgement call.

## Not a second substring matcher

`reconcile._BLOCKER_LINE` WAS a bare substring match over prose, and it
deadlocked a live epic for five days by reading a dependency out of the
sentence "neither depends on the other" — five frozen cards and ~480
consecutive green sweeps (DRE-2670, anchored 2026-08-23, a day before this card
was written). That grammar now lives in `scripts/blocker_prose.py`, read by
every consumer instead of copied into each (DRE-2922). Do not build a second
one. Three things here descend from that:

  * every title pattern must be ANCHORED, and `config_problems()` refuses one
    that is not;
  * every title convention ships an ADVERSARIAL fixture — a title that mentions
    the token without declaring it — and `config_problems()` refuses a
    convention without one, so the mutation test cannot be forgotten;
  * the criteria rule reads CHECKBOX CRITERIA only, never free prose, and
    matches whole words, so a sentence that merely mentions signing in is not
    a card that requires signing in.

## The split this card had wrong first time round

Static visual fidelity is FLEET-checkable: `qa-review.yml` runs a visual-QA
stage (DRE-1481) that screenshots the changed screens and hands the critic both
the design PNG and the render. Interactive or live-state behaviour is
WORKBENCH. **Screenshotting a screen is not driving a flow.**

## And the phrases are read off real cards, not imagined (DRE-2831)

The rule above was right and the FLEET half of it still almost never fired:
`static_visual` matched `pixel-perfect`, `visual parity`, `matches the design`
— phrases that appear in ZERO of the 1,561 carded issues in this workspace, so
real UI cards fell through to a model, at cost, on the exact case the rule
exists to decide for free. What this workspace actually writes is `renders`
(184 cards), `rendered` (46), `design tokens` (23).

The remedy is not a longer guessed list, which is the same defect made longer.
Every phrase now names the real cards that write it (`phrase_evidence`), and
`config_problems()` refuses one that names none. `shows` (189 cards) was tested
and rejected: it reads the same on `synth shows` and `the log shows`, so it
cannot tell a screen from a CLI. The corpus, the verbatim criteria and the real
card bodies are in `tests/fixtures/routing-criteria-corpus-2026-08-31.json`.

## Epics

"Could an agent build this unattended" is meaningless for a card the planner
owns. `route()` returns no verdict for an epic; `plan_test()` asks the plan
questions instead.

CLI:

    python3 scripts/routing_verdict.py render         # rewrite the document
    python3 scripts/routing_verdict.py check          # validate the file
    python3 scripts/routing_verdict.py classify --title T --body-file F \\
        [--label L ...] [--has-children]              # print the decision as JSON
    python3 scripts/routing_verdict.py stamp DRE-N VERDICT --why "…"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402
import mid_epic  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CONFIG_PATH = os.path.join(ROOT, "config", "routing-verdicts.json")
DOC_PATH = os.path.join(ROOT, "docs", "routing-verdicts.md")

# The machine-readable record. Same shape as `mid_epic.VERDICT_TAG` and
# `dead_run.DEAD_TAG`, which reconcile already counts occurrences of — reuse
# the pattern, do not invent one.
VERDICT_TAG = "routing-verdict"
VERDICT_MARK = "🧭"

# The sweep's refusal receipt, surfaced once per card. Deliberately NOT the
# verdict marker: the notice NAMES the verdict it is refusing, and a reader
# that matched the marker anywhere in a body would read this notice back as the
# verdict itself.
NOT_FLEET_TAG = "routing-not-fleet"

# The other refusal, and a DIFFERENT fact: the card is parentless and carries no
# verdict at all, so nothing has approved it (DRE-2735). Separate tag because
# "routed somewhere else on purpose" and "nobody has routed this" have different
# next actions, and a sweep log that collapsed them would tell a reader neither.
NO_VERDICT_TAG = "routing-no-verdict"

# The marker must OPEN the comment. Anchored for the reason above, and for the
# same reason the title patterns are.
_VERDICT_LINE = re.compile(
    rf"^\s*{VERDICT_MARK}\s*{VERDICT_TAG}:\s*\**([A-Z][A-Z ]*[A-Z])\b"
)

# Acceptance criteria are checkbox items and nothing else — that is what
# standards/card-quality.md requires a card to carry, and reading prose instead
# is how a mention becomes a declaration.
_CRITERION = re.compile(r"^\s*[-*+]\s*\[[ xX]\]\s*(.+?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")

# Inheritable labels a planner's child must carry for the epic's plan to be
# executable at all: the repo it routes to and the role that builds it.
_INHERITABLE = ("repo:", "agent:")


class RoutingError(RuntimeError):
    """The vocabulary file is malformed, or a verdict was written without the
    reason it owes. Raised rather than defaulted: a vocabulary that silently
    loses a field is a vocabulary that silently stops routing."""


class UnknownVerdict(Exception):
    """A verdict name the vocabulary does not carry.

    Raised rather than guessed. Defaulting an unknown verdict to promotable
    dispatches work nobody classified; defaulting it to not-promotable freezes
    a card with nothing saying why.
    """


class ConflictingVerdicts(Exception):
    """One card, two different verdicts. Every card leaving Planning carries
    exactly ONE, and a reader that picked between them would be inventing the
    routing decision rather than reading it."""


# --------------------------------------------------------------------------- #
# loading                                                                      #
# --------------------------------------------------------------------------- #

_CACHE: dict = {}


def load(path: str | None = None) -> dict:
    """Parse the vocabulary. Cached per path — `reconcile.promote_ready` reads
    it once per Backlog card and a file read per card is a file read per card."""
    path = path or CONFIG_PATH
    if path not in _CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                _CACHE[path] = json.load(fh)
        except (OSError, ValueError) as e:
            raise RoutingError(f"cannot read the routing vocabulary at {path}: {e}") from e
    return _CACHE[path]


def _records(doc: dict | None = None) -> tuple:
    return tuple((doc or load())["verdicts"])


def verdicts(doc: dict | None = None) -> tuple:
    """The route names, in the file's own order."""
    return tuple(record["name"] for record in _records(doc))


def record(name: str, doc: dict | None = None) -> dict:
    for candidate in _records(doc):
        if candidate["name"] == name:
            return candidate
    raise UnknownVerdict(
        f"{name!r} is not a routing verdict — the five are {', '.join(verdicts(doc))}"
    )


def destination(name: str, doc: dict | None = None) -> str:
    """The lane this verdict sends the card to."""
    return record(name, doc)["destination"]


def actor(name: str, doc: dict | None = None) -> str:
    """Who is accountable for the card at that destination. Every verdict names
    one — a route with no actor is the dead end the amendment to DRE-2724
    removed — and it must be a permitted writer OF THAT LANE (DRE-2824), not
    merely a writer somewhere.

    Usually that is whoever picks the card up. For PARKED nobody does, which is
    the entire point of the verdict, so the actor is the writer that performs
    the move; who revives the card is `revival()`, a different question.
    """
    return record(name, doc)["actor"]


def revival(name: str, doc: dict | None = None) -> str | None:
    """Who takes a card back OUT of this route, when the answer is not the
    actor. None for every route somebody already picks up.

    PARKED carries it. `operator` used to sit in PARKED's actor field to say
    "only a human revives it", which collapsed two different questions into one
    field and sent the card to a lane that actor may not write (DRE-2824). The
    sentence is not lost — it is stated here, in the card comment, in the
    sweep's refusal notice and in the rendered document.
    """
    return record(name, doc).get("revival")


def is_promotable(name: str, doc: dict | None = None) -> bool:
    """May the sweep promote and dispatch this card? FLEET alone."""
    return bool(record(name, doc)["promotable"])


def marks(name: str, doc: dict | None = None) -> tuple:
    """The labels the stamp applies — the signals the rest of the pipeline
    already reads, never a new parallel vocabulary."""
    return tuple(record(name, doc)["marks"])


def title_conventions(doc: dict | None = None) -> tuple:
    return tuple((doc or load())["title_conventions"])


def label_map(doc: dict | None = None) -> dict:
    return dict((doc or load())["labels"]["map"])


def precedence(doc: dict | None = None) -> tuple:
    return tuple((doc or load())["precedence"])


def _signals(doc: dict | None = None) -> tuple:
    """(key, signal) in the file's declared order — the order IS the priority."""
    block = (doc or load())["criteria_signals"]
    return tuple((key, block["signals"][key]) for key in block["order"])


def phrase_evidence(phrase: str, key: str | None = None, doc: dict | None = None) -> tuple:
    """The real cards that write `phrase`, as identifiers — empty if none do.

    The vocabulary's phrases are not guesses (DRE-2831): each one was read off
    the workspace's own acceptance criteria, and each one names the cards it
    was read from. `config_problems()` refuses a phrase that names none, so the
    imagined-phrase defect cannot come back quietly.
    """
    for candidate, signal in _signals(doc):
        if key is not None and candidate != key:
            continue
        entry = ((signal.get("evidence") or {}).get("phrases") or {}).get(phrase)
        if entry:
            return tuple(entry.get("examples") or ())
    return ()


# --------------------------------------------------------------------------- #
# the file checks itself                                                       #
# --------------------------------------------------------------------------- #


def config_problems(doc: dict | None = None) -> list:
    """Everything wrong with the vocabulary file, or an empty list.

    This is the half that keeps the next convention from being written as a
    substring match, and the half that binds every destination and actor to the
    lane contract instead of to a word somebody liked.
    """
    doc = doc if doc is not None else load()
    problems: list[str] = []
    try:
        lanes = set(lane_contract.lane_names(status="live"))
        writers = set(lane_contract.writers())
    except Exception as e:  # noqa: BLE001 — an unreadable contract is a problem, not a crash
        return [f"the lane contract could not be read, so nothing can be bound to it: {e}"]

    names = verdicts(doc)
    for name in names:
        entry = record(name, doc)
        for key in ("means", "destination", "actor", "why"):
            if not (entry.get(key) or "").strip():
                problems.append(f"verdict {name!r} says nothing for {key!r}")
        if entry.get("destination") not in lanes:
            problems.append(
                f"verdict {name!r} routes to {entry.get('destination')!r}, which is "
                "not a live lane in config/lane-contract.json — a destination "
                "nothing can reach is a dead end"
            )
        if entry.get("actor") not in writers:
            problems.append(
                f"verdict {name!r} names the actor {entry.get('actor')!r}, which is "
                "not in the lane contract's writer glossary — a route with no "
                "actor is a card nobody is coming for"
            )
        elif entry.get("destination") in lanes:
            # The STRONG half (DRE-2824). Being a writer somewhere is not being
            # allowed to write THIS lane: PARKED routed to Backlog naming
            # `operator`, and Backlog permits no human writer, so the card was
            # sent to a lane the actor named to handle it could not touch.
            permitted = lane_contract.lane_writers(entry["destination"])
            if entry["actor"] not in permitted:
                problems.append(
                    f"verdict {name!r} names the actor {entry['actor']!r} on the "
                    f"destination {entry['destination']!r}, which permits only "
                    f"{', '.join(permitted)} — an actor the destination lane does "
                    "not permit cannot legally act on the card it is sent"
                )
        if "revival" in entry and not (entry.get("revival") or "").strip():
            problems.append(
                f"verdict {name!r} declares a 'revival' field and says nothing in "
                "it — the field exists to state who takes the card back out"
            )
        if not isinstance(entry.get("promotable"), bool):
            problems.append(f"verdict {name!r} does not say whether it is promotable")

    promotable = [n for n in names if is_promotable(n, doc)]
    if promotable != ["FLEET"]:
        problems.append(
            f"exactly one verdict may be dispatched unattended; {promotable} are"
        )

    for label, verdict in label_map(doc).items():
        if verdict not in names:
            problems.append(f"the label {label!r} maps to the unknown verdict {verdict!r}")
        if label != label.lower():
            problems.append(f"the label {label!r} must be written lower-case")

    for convention in title_conventions(doc):
        pattern = convention.get("pattern") or ""
        who = convention.get("verdict")
        if who not in names:
            problems.append(f"the title convention {pattern!r} maps to unknown {who!r}")
        if not pattern.startswith("^"):
            problems.append(
                f"the title convention {pattern!r} is not anchored at the start of "
                "the title — an unanchored convention is a substring search, and a "
                "substring search over prose froze five cards for five days"
            )
            continue
        if not convention.get("adversarial"):
            problems.append(
                f"the title convention {pattern!r} ships no adversarial fixture — "
                "every title match owes a mutation test whose fixture is a title "
                "that mentions the token without declaring it"
            )
        if title_verdict(convention.get("example") or "", doc) != who:
            problems.append(
                f"the title convention {pattern!r} does not match its own example "
                f"{convention.get('example')!r}"
            )
        for hostile in convention.get("adversarial") or ():
            if title_verdict(hostile, doc) is not None:
                problems.append(
                    f"the adversarial title {hostile!r} matches the {pattern!r} "
                    "convention — it only mentions the token, it does not declare it"
                )

    for key, signal in _signals(doc):
        if signal.get("verdict") not in names:
            problems.append(f"the {key!r} signal maps to unknown {signal.get('verdict')!r}")
        if not signal.get("phrases"):
            problems.append(f"the {key!r} signal declares no phrases")
        if not (signal.get("why") or "").strip():
            problems.append(f"the {key!r} signal says nothing about why it routes there")
        problems.extend(_evidence_problems(key, signal))
    return problems


def _evidence_problems(key: str, signal: dict) -> list:
    """The half that keeps the next phrase list from being written from
    imagination (DRE-2831).

    `static_visual` shipped nine phrases, six of which appear in ZERO of the
    1,561 carded issues in this workspace, so the FLEET half of the rule almost
    never fired and real UI cards were decided by a model — at cost, on the
    exact case the rule exists to decide for free. A longer guessed list is the
    same defect made longer, so a phrase may only enter the vocabulary naming
    the real cards that write it, and the count it was read at.
    """
    problems: list[str] = []
    evidence = signal.get("evidence") or {}
    if not (evidence.get("read_on") or "").strip():
        problems.append(
            f"the {key!r} signal's evidence does not say when the cards were "
            "read — evidence with no date cannot be re-checked"
        )
    if not (evidence.get("corpus") or "").strip():
        problems.append(
            f"the {key!r} signal's evidence does not say WHICH cards were read"
        )
    attested = evidence.get("phrases") or {}
    for phrase in signal.get("phrases") or ():
        entry = attested.get(phrase) or {}
        examples = [e for e in (entry.get("examples") or []) if (e or "").strip()]
        if not examples:
            problems.append(
                f"the {key!r} phrase {phrase!r} names no real card that writes "
                "it — a phrase a card author only imagined never fires, and the "
                "half of the rule that owns it silently becomes a model call "
                "(DRE-2831)"
            )
        if not isinstance(entry.get("cards"), int):
            problems.append(
                f"the {key!r} phrase {phrase!r} does not say how many cards were "
                "found to write it"
            )
    for phrase in attested:
        if phrase not in (signal.get("phrases") or ()):
            problems.append(
                f"the {key!r} signal carries evidence for {phrase!r}, which is "
                "not one of its phrases"
            )
    return problems


# --------------------------------------------------------------------------- #
# reading a card                                                               #
# --------------------------------------------------------------------------- #


def verdict_comment(name: str, why: str, doc: dict | None = None) -> str:
    """The comment that IS the verdict. One card, one of these."""
    entry = record(name, doc)
    if not (why or "").strip():
        raise RoutingError(
            f"a {name} verdict must say why. A routing decision with no reason "
            "cannot be argued with, and NEEDS WORK with no reason names no "
            "missing thing for the planner to add."
        )
    lines = [
        f"{VERDICT_MARK} {VERDICT_TAG}: **{name}** — {entry['means']}",
        "",
        f"**Why:** {why.strip()}",
        "",
        f"**Where it goes:** {entry['destination']}. "
        f"**Who handles it there:** {entry['actor']}.",
    ]
    if entry.get("revival"):
        lines.append(f"**Who takes it back out:** {entry['revival']}")
    if entry["marks"]:
        lines.append(
            "**Marked:** " + ", ".join(f"`{m}`" for m in entry["marks"])
            + " — so the sweep neither dispatches a run nor reports the card as stalled."
        )
    if not entry["promotable"]:
        lines.append(
            "This card is **not** dispatched to the fleet: the promoter reads "
            "this verdict and leaves it where a person will find it."
        )
    return "\n".join(lines)


def verdicts_on(comment_bodies, doc: dict | None = None) -> tuple:
    """Every DISTINCT verdict stamped on a card, in the order first seen.

    The marker must OPEN a comment. A body that merely quotes a verdict — the
    sweep's own refusal notice does exactly that — carries no verdict.
    """
    known = verdicts(doc)
    seen: list[str] = []
    for body in comment_bodies or ():
        match = _VERDICT_LINE.match((body or "").lstrip())
        if not match:
            continue
        name = match.group(1).strip()
        if name in known and name not in seen:
            seen.append(name)
    return tuple(seen)


def verdict_on(comment_bodies, doc: dict | None = None) -> str | None:
    """The card's single verdict, or None. Two different ones raise."""
    found = verdicts_on(comment_bodies, doc)
    if not found:
        return None
    if len(found) > 1:
        raise ConflictingVerdicts(
            "this card carries " + " and ".join(found) + " — a card leaving "
            "Planning carries exactly one verdict, and picking between two "
            "would be inventing the decision rather than reading it"
        )
    return found[0]


def is_parked(comment_bodies, doc: dict | None = None) -> bool:
    """Is this card deliberately not to be built?

    Read by every stall sweep. A PARKED card is well-formed and sitting still
    ON PURPOSE, so reporting it as stalled is reporting the intended state as a
    defect — and a stall report costs the card a hold label a human has to
    remove. Reads the marker, never "somebody said parked".
    """
    return "PARKED" in verdicts_on(comment_bodies, doc)


def stamp_refusal(name: str, comment_bodies, doc: dict | None = None) -> str | None:
    """Why this verdict must not be written, or None to write it.

    Pre-write on purpose: a second verdict posted and then questioned is
    already on the card, and the card already has two.
    """
    record(name, doc)  # raises UnknownVerdict
    found = verdicts_on(comment_bodies, doc)
    if not found:
        return None
    if found == (name,):
        return f"this card already carries a {name} verdict — nothing to add"
    return (
        f"this card already carries {' and '.join(found)}; refusing to add {name}. "
        "A card leaving Planning carries exactly one verdict. If the routing "
        "decision changed, say so on the card and let a human retire the old one."
    )


def promotion_refusal(identifier: str, comment_bodies, doc: dict | None = None) -> str | None:
    """Why the sweep must not promote `identifier`, or None to let it through.

    A card with NO verdict promotes exactly as it did before this card shipped:
    the lane contract's "Backlog entrance: it carries a verdict" clause is
    enforced from Phase 5, and refusing every verdictless card today would
    freeze the board rather than route it. What this refuses is a WRONG
    DESTINATION — a card whose own verdict says a person builds it.
    """
    try:
        name = verdict_on(comment_bodies, doc)
    except ConflictingVerdicts as e:
        return (
            f"🚨 {NOT_FLEET_TAG}: {identifier} is not being promoted — {e}. "
            "Nothing is dispatched until the card says once where it goes."
        )
    if name is None or is_promotable(name, doc):
        return None
    entry = record(name, doc)
    revived = f"**Who takes it back out:** {entry['revival']}\n\n" if entry.get("revival") else ""
    return (
        f"🚨 {NOT_FLEET_TAG}: {identifier} is routed **{name}** — {entry['means']} "
        f"The fleet is not being sent at it.\n\n"
        f"**Where it goes:** {entry['destination']}. "
        f"**Who handles it there:** {entry['actor']}.\n\n"
        f"{revived}"
        f"{entry['why']}\n\n"
        "If that routing is wrong, the card needs a different verdict — not a "
        "nudge from the sweep."
    )


def parentless_promotion_refusal(
    identifier: str, comment_bodies, doc: dict | None = None
) -> str | None:
    """`promotion_refusal` for a card with NO parent epic (DRE-2735).

    A child's approval is its epic's state: a human moved that epic, and that
    decision covers everything under it. A one-off has no such approval to
    inherit, so the verdict IS the approval — written at Planning exit, which is
    the design's whole claim about why a one-off never reaches the CEO. Absent
    it, nothing has approved this card and the sweep must not dispatch it.

    The wrong-destination refusal is unchanged and takes precedence: "routed
    WORKBENCH" and "routed nowhere" are different facts, and the first already
    says who picks the card up instead.
    """
    refusal = promotion_refusal(identifier, comment_bodies, doc)
    if refusal is not None:
        return refusal
    if verdict_on(comment_bodies, doc) is not None:
        return None
    return (
        f"🚨 {NO_VERDICT_TAG}: {identifier} has no parent epic and carries no "
        "routing verdict, so nothing has approved it — the sweep is not "
        "promoting it.\n\n"
        "A card under an epic inherits that epic's approval: a human moved the "
        "epic, and the sweep reads its state. A one-off inherits nothing, so "
        "its verdict is the approval, and it is written at Planning exit.\n\n"
        "**To let it through:** stamp the routing decision —\n"
        "`python3 scripts/routing_verdict.py stamp <CARD> FLEET "
        '--why "<one line>"`\n\n'
        "This refusal is only about carrying NO verdict. A verdict that is not "
        "FLEET routes the card somewhere else on purpose, and says where."
    )


def refusal_tag(refusal: str | None) -> str | None:
    """The idempotency tag `refusal` is surfaced under, or None if it is not
    one of this module's refusals.

    Every refusal here OPENS with its own tag, so the tag is read off the
    notice rather than inferred by the caller from context. That matters
    because the sweep posts each refusal at most once, keyed on the tag: pair a
    notice with the wrong tag and the two refusals silence each other.
    """
    first = ((refusal or "").splitlines() or [""])[0]
    for tag in (NO_VERDICT_TAG, NOT_FLEET_TAG):
        if first.startswith(f"🚨 {tag}:"):
            return tag
    return None


# --------------------------------------------------------------------------- #
# routing a card                                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Decision:
    """One routing decision, and what produced it.

    `verdict` is None in exactly two cases, and they are different: an EPIC,
    which is never given a buildability verdict at all, and a JUDGEMENT call,
    which is the one case where a model is worth asking.
    """

    verdict: str | None
    source: str  # "label" | "title" | "criteria" | "judgement" | "epic"
    reason: str
    needs_model: bool = False
    plan_questions: tuple = field(default_factory=tuple)


def acceptance_criteria(description: str) -> list:
    """The card's stated exit condition: its checkbox items, fenced code out.

    Fenced blocks are skipped for the same reason the repo-stamp reader skips
    them — a criterion inside a fence is an example of a criterion.
    """
    out: list[str] = []
    fenced = False
    for line in (description or "").splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _CRITERION.match(line)
        if match:
            out.append(match.group(1))
    return out


def _phrase_re(phrase: str) -> re.Pattern:
    """A whole-phrase matcher: internal whitespace is flexible, the edges are
    hard. `sign in` must not fire on `sign-in`, and `log in` must not fire on
    `login` — those two are exactly the static-visual criteria this rule has to
    leave alone."""
    body = re.escape(phrase).replace("\\ ", r"\s+")
    return re.compile(rf"(?<![\w-]){body}(?![\w-])", re.IGNORECASE)


_PHRASE_CACHE: dict = {}


def _matches(text: str, phrases) -> str | None:
    for phrase in phrases:
        pattern = _PHRASE_CACHE.get(phrase)
        if pattern is None:
            pattern = _PHRASE_CACHE[phrase] = _phrase_re(phrase)
        if pattern.search(text):
            return phrase
    return None


def label_verdict(labels, doc: dict | None = None) -> str | None:
    """Precedence 1. An EXACT lower-cased label match — `no-codegen` is not
    `no-code`."""
    mapping = label_map(doc)
    for label in labels or ():
        hit = mapping.get((label or "").strip().lower())
        if hit:
            return hit
    return None


def title_verdict(title: str, doc: dict | None = None) -> str | None:
    """Precedence 2. Anchored at the start of the title, case-insensitive.
    Never a substring search — see the module docstring."""
    for convention in title_conventions(doc):
        if re.match(convention["pattern"], title or "", re.IGNORECASE):
            return convention["verdict"]
    return None


def criteria_verdict(description: str, doc: dict | None = None) -> tuple:
    """Precedence 3, as `(verdict, reason)`.

    `(None, reason)` means the criteria exist and name neither signal — a
    judgement call, and the only place a model is worth asking.
    """
    criteria = acceptance_criteria(description)
    if not criteria:
        return (
            "NEEDS WORK",
            "the card states no acceptance criteria, so there is no exit "
            "condition to route on. Name what must be true for this card to be "
            "done, as `- [ ]` items.",
        )
    for key, signal in _signals(doc):
        for criterion in criteria:
            hit = _matches(criterion, signal["phrases"])
            if hit:
                return (
                    signal["verdict"],
                    f"a criterion says {hit!r} — {signal['why']}",
                )
    return (
        None,
        "the acceptance criteria name neither an interactive flow nor a static "
        "visual comparison, so whether an unattended agent can satisfy them is "
        "a judgement call.",
    )


def plan_test(description: str, children_labels, doc: dict | None = None) -> tuple:
    """What an epic's PLAN is missing, or an empty tuple.

    Never a buildability test: "could an agent build this unattended" is
    meaningless for a card the planner owns, and answering it anyway is how a
    routing vocabulary quietly becomes a quality score.
    """
    missing: list[str] = []
    children = list(children_labels or [])
    if not children:
        missing.append(
            "it has no children — a plan is a set of cards, and this is one card"
        )
    for index, labels in enumerate(children, 1):
        lowered = [(l or "").lower() for l in (labels or [])]
        absent = [p for p in _INHERITABLE if not any(l.startswith(p) for l in lowered)]
        if absent:
            missing.append(
                f"child {index} carries no {' and no '.join(repr(p) for p in absent)} "
                "label — a child with nothing inheritable routes nowhere"
            )
    if not acceptance_criteria(description):
        missing.append(
            "it states no acceptance criterion for the set — an epic whose "
            "children each pass and whose whole is untested is a plan nobody "
            "can call finished"
        )
    return tuple(missing)


def route(title, description, labels=(), has_children: bool = False,
          doc: dict | None = None) -> Decision:
    """The routing decision for one card, in strict precedence."""
    labels = list(labels or [])
    if mid_epic.is_epic(title, labels, has_children):
        return Decision(
            verdict=None,
            source="epic",
            reason=(
                "this is an epic — the planner owns it, so it gets a plan test "
                "(children, inheritable labels, an acceptance criterion for the "
                "set) and never a buildability verdict"
            ),
            needs_model=False,
            plan_questions=tuple((doc or load())["epics"]["plan_test"]),
        )

    hit = label_verdict(labels, doc)
    if hit:
        return Decision(
            verdict=hit,
            source="label",
            reason=(
                "the card carries an explicit role label, which says where it "
                "goes — no judgement, and no model asked"
            ),
        )

    hit = title_verdict(title, doc)
    if hit:
        return Decision(
            verdict=hit,
            source="title",
            reason="the title declares a convention, anchored at its start",
        )

    verdict, reason = criteria_verdict(description, doc)
    return Decision(
        verdict=verdict,
        source="judgement" if verdict is None else "criteria",
        reason=reason,
        needs_model=verdict is None,
    )


# --------------------------------------------------------------------------- #
# the rendered document                                                        #
# --------------------------------------------------------------------------- #


def render_markdown(doc: dict | None = None) -> str:
    doc = doc if doc is not None else load()
    out: list[str] = []
    w = out.append
    w("# Routing verdicts")
    w("")
    w("<!-- GENERATED FILE — do not edit. Source: config/routing-verdicts.json.")
    w("     Regenerate with `python3 scripts/routing_verdict.py render`. -->")
    w("")
    w(
        "A verdict is a **routing decision, not a quality score**. It answers "
        "*who builds this, and how* — and every answer sends the card somewhere "
        "different. Framed as a score, a critic drifts toward marking things "
        "good so it looks useful; framed as routing there is no good or bad, "
        "only a wrong destination, which shows up immediately."
    )
    w("")
    w(
        "This document is rendered from the same file the sweep and the write "
        "path read, so it cannot drift from the enforcement. Destinations and "
        "actors are bound to `config/lane-contract.json`: a route whose "
        "destination is not a lane, or whose actor is not a permitted writer of "
        "that lane, fails `python3 scripts/routing_verdict.py check`."
    )
    w("")
    w(
        "The actor is who is **accountable for the card at the destination** — "
        "usually whoever picks it up, and where nobody does, the writer that "
        "performs the move. Being a writer somewhere is not enough: `operator` "
        "is a real writer and is not permitted on `Backlog`, which is why "
        "PARKED naming it sent cards to a lane that actor may not write "
        "(DRE-2824)."
    )
    w("")

    w("## The five routes")
    w("")
    w("| Verdict | Means | Destination | Who handles it there | Dispatched? |")
    w("| --- | --- | --- | --- | --- |")
    for entry in _records(doc):
        w(
            f"| **{entry['name']}** | {entry['means']} | {entry['destination']} | "
            f"`{entry['actor']}` | {'yes' if entry['promotable'] else 'no'} |"
        )
    w("")
    for entry in _records(doc):
        marked = (
            "  Marked " + ", ".join(f"`{m}`" for m in entry["marks"]) + "."
            if entry["marks"]
            else ""
        )
        w(f"- **{entry['name']}** — {entry['why']}{marked}")
        if entry.get("revival"):
            w(f"  - **Who takes it back out:** {entry['revival']}")
    w("")

    w("## The rule, and it is mechanical")
    w("")
    w(
        "Route on whether an unattended agent can **satisfy the acceptance "
        "criteria** — not on whether it could write the code. That reads the "
        "card's own stated exit condition instead of guessing from the title."
    )
    w("")
    w("Read in strict precedence:")
    w("")
    for i, step in enumerate(precedence(doc), 1):
        w(f"{i}. {step}")
    w("")

    w("## Title conventions")
    w("")
    w(
        "Anchored at the start of the title, never a substring search. Each one "
        "ships an **adversarial fixture** — a title that mentions the token "
        "without declaring it — and `config_problems()` refuses a convention "
        "that has none, so the mutation test cannot be forgotten. A bare "
        "substring match over prose is what froze five cards for five days "
        "(DRE-2670)."
    )
    w("")
    w("| Verdict | Pattern | Matches | Must NOT match |")
    w("| --- | --- | --- | --- |")
    for convention in title_conventions(doc):
        hostile = "<br>".join(f"`{t}`" for t in convention["adversarial"])
        w(
            f"| {convention['verdict']} | `{convention['pattern']}` | "
            f"`{convention['example']}` | {hostile} |"
        )
    w("")
    for convention in title_conventions(doc):
        w(f"- `{convention['pattern']}` — {convention['means']}")
    w("")

    w("## Labels read first")
    w("")
    w("| Label | Verdict |")
    w("| --- | --- |")
    for label, verdict in label_map(doc).items():
        w(f"| `{label}` | {verdict} |")
    w("")
    w(
        "Exact match, lower-cased. `no-codegen` is not `no-code`, and reading it "
        "as one is the same mistake class as a substring blocker match, one "
        "field over."
    )
    w("")

    w("## What the acceptance criteria are read for")
    w("")
    w(
        "Checkbox criteria only, never free prose, matched on whole words. "
        "Signals are tried in the order below, and the order is load-bearing: "
        "**screenshotting a screen is not driving a flow**, but driving a flow "
        "that ends at a screen is still driving a flow."
    )
    w("")
    w(
        "**Every phrase names the real cards that write it (DRE-2831).** The "
        "first version of this rule was written from phrases a card author "
        "imagined, and six of the nine visual ones appear in zero of this "
        "workspace's 1,561 carded issues — so the FLEET half almost never "
        "fired and real UI cards were routed by a model instead. A phrase with "
        "no card behind it now fails "
        "`python3 scripts/routing_verdict.py check`."
    )
    w("")
    for key, signal in _signals(doc):
        w(f"### {key} → {signal['verdict']}")
        w("")
        w(signal["why"])
        w("")
        evidence = signal.get("evidence") or {}
        attested = evidence.get("phrases") or {}
        w("| Phrase | Cards that write it | Read from |")
        w("| --- | --- | --- |")
        for phrase in signal["phrases"]:
            entry = attested.get(phrase) or {}
            examples = ", ".join(entry.get("examples") or ()) or "—"
            w(f"| `{phrase}` | {entry.get('cards', '—')} | {examples} |")
        w("")
        if evidence:
            w(f"Read on {evidence.get('read_on')} across {evidence.get('corpus')}.")
            w("")
            w(evidence.get("note", ""))
            w("")
    w(
        "Criteria that name neither signal are a judgement call — the one place "
        "a model is worth asking. A card with no acceptance criteria at all is "
        "NEEDS WORK: there is no exit condition to route on."
    )
    w("")

    w("## Epics get a different question")
    w("")
    w(
        "\"Could an agent build this unattended\" is meaningless for a card the "
        "planner owns. An epic gets a **plan test**, never a buildability test "
        "and never one of the five routes:"
    )
    w("")
    for question in doc["epics"]["plan_test"]:
        w(f"- {question}")
    w("")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _cmd_stamp(identifier: str, name: str, why: str) -> int:
    import linear_ops

    refusal = stamp_refusal(name, linear_ops.comment_bodies(identifier))
    if refusal is not None:
        print(f"refusing to stamp {identifier}: {refusal}", file=sys.stderr)
        return 1
    linear_ops.cmd_comment(identifier, verdict_comment(name, why))
    for label in marks(name):
        linear_ops.add_label(identifier, label)
    print(
        f"stamped {identifier} {name} → {destination(name)} "
        f"(handled there by {actor(name)})"
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("render")
    sub.add_parser("check")

    classify = sub.add_parser("classify")
    classify.add_argument("--title", default="")
    classify.add_argument("--body-file")
    classify.add_argument("--label", action="append", default=[])
    classify.add_argument("--has-children", action="store_true")

    stamp = sub.add_parser("stamp")
    stamp.add_argument("identifier")
    stamp.add_argument("verdict")
    stamp.add_argument("--why", required=True)

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "render":
        rendered = render_markdown()
        with open(DOC_PATH, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        print(f"wrote {DOC_PATH} ({len(rendered.splitlines())} lines)")
        return 0

    if command == "check":
        problems = config_problems()
        for problem in problems:
            print(f"  [FAIL] {problem}")
        print(
            f"{len(verdicts())} route(s) checked against the lane contract, "
            f"{len(problems)} problem(s)"
        )
        return 1 if problems else 0

    if command == "classify":
        body = ""
        if args.body_file:
            with open(args.body_file, encoding="utf-8") as fh:
                body = fh.read()
        decision = route(args.title, body, args.label, has_children=args.has_children)
        print(json.dumps({
            "verdict": decision.verdict,
            "source": decision.source,
            "reason": decision.reason,
            "needs_model": decision.needs_model,
            "destination": destination(decision.verdict) if decision.verdict else None,
            "actor": actor(decision.verdict) if decision.verdict else None,
            "plan_questions": list(decision.plan_questions),
        }, indent=2))
        return 0

    if command == "stamp":
        return _cmd_stamp(args.identifier, args.verdict, args.why)

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
