#!/usr/bin/env python3
"""A roll-up's split into child epics — checked, then activated (DRE-4717).

A card too big for one epic is a ROLL-UP: the planner splits it into child
epics under itself, each planned and green-lit on its own, and the original
stays as the parent that holds them (`standards/card-quality.md`, "What the
planner files instead"). This module is to that route what `proof_and_demo.py`
is to the epic route: it reads the children the planner actually created, out
of Linear, and never the brief's description of them.

## `check` — is this a split at all

The records come in on stdin from `linear_ops.py children-detail <PARENT>`,
the same shape-on-stdin contract `proof_and_demo.py check` reads. Seven
findings, each named so the bounce comment can quote it:

  * `too-few-children` — one child is an epic, not a split.
  * `not-titled-epic` — every child is titled `[EPIC] …`, anchored at the start.
  * `no-planner-label` — every child carries the epic shape's own mark
    (`agent:planner`, read off `config/planning-shapes.json`), because that is
    what keeps the sweep from promoting a container as work.
  * `wears-build-role` — no child carries a role a build run is dispatched
    for. The roles are `proof_and_demo.build_roles()`, read off the roster,
    never restated here.
  * `no-slice-or-criteria` — every child body states its slice in a prose
    paragraph ahead of its own `## Acceptance criteria` heading.
  * `blocked-by-cycle` — the sibling `blockedBy` relations are acyclic.
  * `no-first-child` — at least one child waits on nothing inside the roll-up,
    its siblings or the parent itself. Acyclic sibling relations always leave
    one child with no sibling ahead of it, so what this adds is the parent: a
    child blocked by its own epic waits on a card that closes only when that
    child does, which is the deadlock the standard already forbids.

The ORDER it prints on a pass is the topological order of the sibling
`blockedBy` relations, ties broken by creation order. That one order is the
one the receipt lists and `activate` moves the children in.

A finding names cards by identifier and never quotes a title. The bounce is
posted to the parent, and a title quoted into it could carry the receipt's own
tag onto the card — which would read as a split already recorded.

## `activate` — record it once, and put every child where its planner starts

It reads the parent and its children live, re-runs the check (nothing moves on
a finding), and then, in this order:

  1. posts ONE receipt on the parent — `🧩 roll-up-split:`, composed through
     `pipeline_act.receipt()` and keyed on its tag, so a retried run posts
     nothing twice. It names every child in order with its slice's first
     sentence and what blocks it, and ends with what the parent is;
  2. moves every child still in Backlog to Planning, in that order. Arriving in
     Planning is what starts a child's planner run — the relay dispatches one
     per lane entry (DRE-1913, DRE-3030); nothing here dispatches. A child
     already past Backlog is left alone;
  3. moves the parent from Planning to In Progress — the lane the sweep reads
     active epics from (`reconcile.repo_epics` → `close_finished_epics`), so
     the parent closes by itself when its last child is Done
     (`reconcile._close_epic_if_finished`, unchanged) and `promote_ready`
     skips it as an epic. The CEO never approves a roll-up parent, so nothing
     else would move it there.

The parent moves LAST on purpose. An active epic's Backlog children are what
the sweep promotes, so the parent reaching In Progress while its children still
sat in Backlog would put them in front of the promotion gate instead of the
planner. A crash anywhere leaves a state the retry finishes: the receipt is
counted before it is posted, and `cmd_advance` moves a card only out of the
lane it names.

The lane names are read off the contract (`lane_contract.lane_names`) at the
moment they are used, and `contract_problems()` holds the contract to the
writes this makes: `plan.yml` — the run that splits a roll-up — must be a
permitted writer of both destinations.

Every Linear-touching function takes the `linear_ops` MODULE as its first
argument — the convention `mid_epic` uses — so the pure
core needs no API key and the tests need no network.

CLI:

    python3 scripts/linear_ops.py children-detail DRE-N \\
      | python3 scripts/epic_split.py check --epic DRE-N [--comment-file F]
    python3 scripts/epic_split.py activate DRE-N
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402
import pipeline_act  # noqa: E402
import planning_shape  # noqa: E402
import proof_and_demo  # noqa: E402
import sanitize_untrusted  # noqa: E402

# The act the receipt is composed as, and its idempotency key — both declared
# in `config/pipeline-acts.json`. The mark is a new one: every existing mark
# already means something the pipeline reads.
ACT = "roll-up-activated"
SPLIT_TAG = "roll-up-split"
SPLIT_MARK = "🧩"

# The bounce's marker. Deliberately shares nothing with SPLIT_TAG: the receipt
# is counted by substring on the parent, and a bounce that carried the tag would
# read as a split already recorded.
BOUNCE_KEY = "roll-up-owes-child-epics"
BOUNCE_MARK = "🧾"

# The sentence the receipt ends on — what the parent is, where the person
# reading the card reads it.
PARENT_SENTENCE = (
    "This parent is never approved for building, builds nothing itself, and "
    "closes when every child is Done."
)

# The workflow that runs this module, and so the writer the lane contract has
# to permit for both destinations.
WRITER = "plan.yml"

# The shape whose mark every child carries. Read from
# `config/planning-shapes.json`, never spelled here.
EPIC_SHAPE = "epic"

EPIC_PREFIX = "[EPIC]"
_EPIC_TITLE = re.compile(r"^\s*\[EPIC\]")
_ACCEPTANCE = re.compile(r"^##\s+Acceptance criteria\s*$", re.IGNORECASE)

# Lines that open a block which is not a prose paragraph: a heading, a list
# item, a quote, a table, a fence, an HTML comment, and a bold label line such
# as `**Files:** …` or `**Blocked by:** …`.
_NOT_PROSE = re.compile(
    r"^\s*(?:#|[-*+]\s|\d+[.)]\s|>|\||```|~~~|<!--|\*\*[^*]+:\*\*)"
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_SENTENCE_CHARS = 240


class SplitError(RuntimeError):
    """The split cannot be evaluated — a lane contract that no longer names a
    lane this writes, a children read that returned nothing parseable. Raised
    rather than defaulted: guessing here either moves cards nobody decided to
    move or leaves a roll-up parked with nothing saying why."""


@dataclass(frozen=True)
class Finding:
    """One defect in the split, under the name the bounce quotes."""

    name: str
    text: str

    def __str__(self) -> str:
        return f"`{self.name}` {self.text}"


# --------------------------------------------------------------------------- #
# the lanes, read off the contract                                             #
# --------------------------------------------------------------------------- #


def _lane(name: str, contract: dict | None = None) -> str:
    if name not in lane_contract.lane_names(contract=contract):
        raise SplitError(
            f"config/lane-contract.json carries no live lane {name!r}, so a "
            "split has nowhere to put its cards"
        )
    return name


def lanes(contract: dict | None = None) -> dict:
    """Every lane this module reads or writes, each confirmed live in the
    contract at the moment it is used."""
    return {
        "child_from": _lane("Backlog", contract),
        "child_to": _lane("Planning", contract),
        "parent_from": _lane("Planning", contract),
        "parent_to": _lane("In Progress", contract),
    }


def destinations(contract: dict | None = None) -> tuple:
    """Every lane this module can put a card in.

    `activate` writes lanes it reads off the contract, so the destination is
    not readable at the call site; `ready_lane_writers.py` (DRE-2859) asks for
    it here instead, by name.
    """
    named = lanes(contract)
    return tuple(dict.fromkeys((named["child_to"], named["parent_to"])))


def contract_problems(contract: dict | None = None) -> list:
    """Everything wrong with the contract as a permit for these writes."""
    try:
        named = lanes(contract)
    except SplitError as e:
        return [str(e)]
    out = []
    for key in ("child_to", "parent_to"):
        lane = named[key]
        if WRITER not in lane_contract.lane_writers(lane, contract=contract):
            out.append(
                f"{WRITER} is not a permitted writer of {lane!r} in "
                "config/lane-contract.json, and activating a roll-up's split "
                f"writes it — the {key.replace('_', ' ')} lane"
            )
    return out


def _epic_mark() -> str:
    marks = planning_shape.marks(EPIC_SHAPE)
    if not marks:
        raise SplitError(
            f"the {EPIC_SHAPE!r} shape in config/planning-shapes.json applies "
            "no marks, so nothing would make a child epic an epic"
        )
    return marks[0]


# --------------------------------------------------------------------------- #
# reading one roll-up's children                                               #
# --------------------------------------------------------------------------- #


def _ident(card: dict) -> str:
    return (card or {}).get("identifier") or "(unidentified card)"


def slice_paragraph(body: str) -> str | None:
    """The first prose paragraph ahead of `## Acceptance criteria`, or None
    when there is no such heading or nothing prose stands before it."""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    heading = next((i for i, l in enumerate(lines) if _ACCEPTANCE.match(l)), None)
    if heading is None:
        return None
    # Blocks separated by blank lines; a fenced block is never prose, blank
    # lines inside it included.
    blocks: list[list[str]] = [[]]
    fenced = False
    for line in lines[:heading]:
        if line.strip().startswith(("```", "~~~")):
            if fenced:
                blocks[-1].append(line)
                blocks.append([])
            else:
                blocks.append([line])
            fenced = not fenced
            continue
        if fenced:
            blocks[-1].append(line)
            continue
        if not line.strip():
            blocks.append([])
            continue
        blocks[-1].append(line)
    for block in blocks:
        if block and not _NOT_PROSE.match(block[0]):
            return " ".join(line.strip() for line in block)
    return None


def first_sentence(body: str) -> str:
    """The slice's first sentence, safe to post: one line, defanged, bounded.

    Card text is data (`standards/untrusted-content.md`), so what the receipt
    quotes goes through `sanitize_untrusted.sanitize_line` — never a raw body.
    """
    paragraph = slice_paragraph(body) or ""
    sentence = _SENTENCE_END.split(" ".join(paragraph.split()), maxsplit=1)[0]
    if len(sentence) > _SENTENCE_CHARS:
        sentence = sentence[: _SENTENCE_CHARS - 1].rstrip() + "…"
    return sanitize_untrusted.sanitize_line(sentence)


def _sibling_blockers(children: list) -> dict:
    ids = {_ident(c) for c in children}
    return {
        _ident(c): [b for b in (c.get("blocked_by") or []) if b in ids]
        for c in children
    }


def order(children: list) -> list:
    """The children's identifiers in topological order of their sibling
    `blockedBy` relations, ties broken by creation order.

    `children` arrive in creation order (`linear_ops.child_detail_records`
    sorts them). A child caught in a cycle, or waiting on one, is left out —
    `findings()` refuses that split before anything reads this order.
    """
    blockers = _sibling_blockers(children)
    placed: list[str] = []
    remaining = [_ident(c) for c in children]
    while remaining:
        ready = next(
            (i for i in remaining if all(b in placed for b in blockers[i])), None
        )
        if ready is None:
            break
        placed.append(ready)
        remaining.remove(ready)
    return placed


def _cycle_members(children: list) -> list:
    """The children that can reach themselves through sibling relations."""
    blockers = _sibling_blockers(children)
    members = []
    for start in blockers:
        seen, stack = set(), list(blockers[start])
        while stack:
            node = stack.pop()
            if node == start:
                members.append(start)
                break
            if node in seen:
                continue
            seen.add(node)
            stack.extend(blockers.get(node, ()))
    return members


def findings(children: list, parent: str | None = None) -> list:
    """Everything wrong with this split, or an empty list.

    `children` are the parent's cards IN CREATION ORDER, each a record from
    `linear_ops.py children-detail`: `identifier`, `title`, `body`, `labels`,
    `blocked_by` (formal `blocks` relations only).
    """
    cards = list(children or [])
    if len(cards) < 2:
        return [Finding(
            "too-few-children",
            f"the roll-up has {len(cards)} child epic(s). A split is two or "
            "more: one child is an epic, not a split — plan the card as that "
            "epic instead.",
        )]

    found: list[Finding] = []
    mark = _epic_mark()
    build = proof_and_demo.build_roles()

    for card in cards:
        ident = _ident(card)
        if not _EPIC_TITLE.match(card.get("title") or ""):
            found.append(Finding(
                "not-titled-epic",
                f"{ident}: its title does not open `{EPIC_PREFIX}`. Every child "
                "of a roll-up is an epic of its own, titled "
                f"`{EPIC_PREFIX} <slug>: …`.",
            ))
        labels = [str(l).strip().lower() for l in card.get("labels") or ()]
        if mark not in labels:
            found.append(Finding(
                "no-planner-label",
                f"{ident}: it does not carry `{mark}`. A child epic is planned, "
                "never promoted as work, and that label is what keeps the sweep "
                "off it.",
            ))
        worn = [l for l in labels if proof_and_demo._role_of(l) in build]
        if worn:
            found.append(Finding(
                "wears-build-role",
                f"{ident}: it carries "
                + ", ".join(f"`{l}`" for l in worn)
                + " — a role a build run is dispatched for. A child epic builds "
                "nothing itself; drop the role "
                f"(`linear_ops.py remove-label {ident} {worn[0]}`).",
            ))
        if slice_paragraph(card.get("body") or "") is None:
            found.append(Finding(
                "no-slice-or-criteria",
                f"{ident}: its body has no `## Acceptance criteria` heading, or "
                "no prose paragraph ahead of it. Each child states its slice in "
                "a short paragraph first, then its own criteria.",
            ))

    cycle = _cycle_members(cards)
    if cycle:
        found.append(Finding(
            "blocked-by-cycle",
            "the sibling `blockedBy` relations form a cycle through "
            + ", ".join(cycle)
            + " — none of them can ever go first. The order between child "
            "epics is a chain, not a loop.",
        ))

    inside = {_ident(c) for c in cards} | ({parent} if parent else set())
    if not any(
        not [b for b in (c.get("blocked_by") or []) if b in inside] for c in cards
    ):
        found.append(Finding(
            "no-first-child",
            "every child waits on another card inside the roll-up — a sibling "
            "or the parent itself — so no child can go first. The first child "
            "is blocked by nothing here, and a child is never blocked by its "
            "own epic.",
        ))
    return found


def bounce_comment(parent: str, found: list) -> str:
    """The note the run posts to the parent when the split is not one.

    The grammar of `proof_and_demo.bounce_comment`: what a roll-up's children
    must be, then what is missing. Raises on an empty finding list — a bounce
    with nothing to say is a plan stopped for no stated reason.
    """
    if not found:
        raise ValueError(
            "refusing to write a bounce with no finding — a roll-up is only "
            "sent back with the reason named"
        )
    roles = ", ".join(f"`agent:{r}`" for r in proof_and_demo.build_roles())
    lines = [
        f"{BOUNCE_MARK} {BOUNCE_KEY}: {parent} stays in **Planning** — its "
        "children are not a split into child epics yet.",
        "",
        "A roll-up is split into **child epics under itself**, and each child "
        "is planned and green-lit on its own. So the children are **two or "
        f"more**, each titled `{EPIC_PREFIX} …`, carrying `{_epic_mark()}` and "
        f"no build role ({roles}), with a short paragraph stating its slice "
        "ahead of its own `## Acceptance criteria`. The order between them is "
        "Linear `blockedBy` relations — a chain with no cycle, whose first "
        "child waits on nothing inside the roll-up.",
        "",
        "**What is missing:**",
        "",
    ]
    lines += [f"- {f}" for f in found]
    lines += [
        "",
        "Fix the children and re-run the planner; the split activates when "
        "nothing above is left.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the receipt                                                                  #
# --------------------------------------------------------------------------- #


def receipt_detail(parent: str, children: list) -> str:
    """The receipt's body, before the trailer: every child in order, its
    slice's first sentence and what blocks it, then what the parent is."""
    by_id = {_ident(c): c for c in children}
    sequence = order(children)
    siblings = _sibling_blockers(children)
    child_lane = lanes()["child_to"]
    lines = [
        f"{SPLIT_MARK} {SPLIT_TAG}: {parent} is split into {len(sequence)} "
        "child epics, each planned and green-lit on its own, in this order:",
        "",
    ]
    for position, ident in enumerate(sequence, 1):
        card = by_id[ident]
        ahead = siblings[ident]
        outside = [b for b in (card.get("blocked_by") or []) if b not in ahead]
        waits = (
            "after " + ", ".join(ahead) if ahead else "nothing ahead of it"
        )
        if outside:
            waits += "; also blocked by " + ", ".join(outside)
        lines.append(
            f"{position}. **{ident}** — {first_sentence(card.get('body') or '')} "
            f"— {waits}"
        )
    lines += [
        "",
        f"Each child still in Backlog moves to `{child_lane}` now, in this "
        "order. Arriving there is what starts its own planner run — the relay "
        "dispatches one for every card that enters the lane, and nothing here "
        "claims that run started.",
        "",
        PARENT_SENTENCE,
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Linear-touching seams                                                        #
# --------------------------------------------------------------------------- #


def read_children(linear_ops, parent: str) -> list:
    """The parent's children exactly as `linear_ops.py children-detail` prints
    them — the same read `check` is fed on stdin, so both commands judge the
    same records."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        linear_ops.cmd_children_detail(parent)
    try:
        records = json.loads(buffer.getvalue() or "[]")
    except ValueError as e:
        raise SplitError(f"the children of {parent} could not be read: {e}") from e
    if not isinstance(records, list):
        raise SplitError(f"the children of {parent} did not read as a list")
    return records


def activate(linear_ops, parent: str) -> int:
    """Record the split once and put every child where its planner starts.

    Returns 0 when the split is active (including a retry that had nothing
    left to do), 1 when nothing moved because the split is refused.
    """
    named = lanes()
    problems = contract_problems()
    if problems:
        raise SplitError("; ".join(problems))

    lane = ((linear_ops.get_issue(parent, fresh=True) or {}).get("state") or {}).get("name")
    if lane not in (named["parent_from"], named["parent_to"]):
        print(
            f"epic split: {parent} is in {lane!r}, not {named['parent_from']!r} "
            "— a split is activated from there, and nothing moved"
        )
        return 1

    children = read_children(linear_ops, parent)
    found = findings(children, parent)
    if found:
        for finding in found:
            print(finding)
        print(f"epic split: {parent} — {len(found)} finding(s), nothing moved")
        return 1

    if linear_ops.count_comments(parent, f"{SPLIT_MARK} {SPLIT_TAG}:") == 0:
        body = pipeline_act.receipt("roll-up-activated", receipt_detail(parent, children))
        linear_ops.cmd_comment(parent, body)
        print(f"epic split: {parent} — receipt posted")
    else:
        print(f"epic split: {parent} — receipt already on the card")

    sequence = order(children)
    for child in sequence:
        linear_ops.cmd_advance(child, named["child_to"], named["child_from"])
    linear_ops.cmd_advance(parent, named["parent_to"], named["parent_from"])
    print(
        f"epic split: {parent} active — {len(sequence)} child epic(s) in order: "
        + ", ".join(sequence)
    )
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _stdin_json(default):
    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not raw:
        return default
    return json.loads(raw)


def _cmd_check(args) -> int:
    children = _stdin_json([])
    found = findings(children, args.epic)
    if not found:
        siblings = _sibling_blockers(children)
        print(
            f"{args.epic}: a roll-up split into {len(children)} child epic(s), "
            "in blockedBy order:"
        )
        for position, ident in enumerate(order(children), 1):
            after = f" (after {', '.join(siblings[ident])})" if siblings[ident] else ""
            print(f"  {position}. {ident}{after}")
        return 0
    for finding in found:
        print(finding)
    if args.comment_file:
        with open(args.comment_file, "w", encoding="utf-8") as fh:
            fh.write(bounce_comment(args.epic, found))
    return 1


def _cmd_activate(args) -> int:
    import linear_ops

    return activate(linear_ops, args.parent)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="the roll-up's children, on stdin")
    check.add_argument("--epic", required=True)
    check.add_argument("--comment-file", default=None,
                       help="where to write the bounce note, when there is one")
    check.set_defaults(fn=_cmd_check)

    act = sub.add_parser("activate", help="record the split and move the cards")
    act.add_argument("parent")
    act.set_defaults(fn=_cmd_activate)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_usage(sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SplitError as e:
        raise SystemExit(f"epic split: {e}")
