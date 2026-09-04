#!/usr/bin/env python3
"""Every epic carries a proof card and a demo card (DRE-2746).

The convention already existed — `standards/plan-artifact.md` gives the plan
artifact a "Proof and demo" section — and nothing made any planner follow it.
A brief is guidance a model can skip, and a convention nothing checks is a
convention that drifts. So the check runs on the PLANNER'S OUTPUT: the cards it
actually created, read out of Linear, never the brief's text.

## The two things, and they are not the same

  * **Proof** answers *did it work* — and it is not a green test suite. It is
    the mechanism observed running against real state, and the observation is
    recorded in the repo so the card produces a written artifact rather than a
    claim.
  * **Demo** answers *can the CEO see it*. A merged PR and a passing suite are
    invisible to the person who green-lit the epic.

An epic that produces neither has no way of being wrong in public.

## What is checked, and why each half is here

  1. A `PROOF:` card and a `DEMO:` card exist, and they are the LAST two
     children. Position is not decoration: the pair closes the epic.
  2. Both are blocked by every OTHER child — read off Linear's formal `blocks`
     relations, never off the order the cards sit in and never off the
     `**Blocked by:**` prose line. Prose leaves the reconcile gates blind
     (DRE-2670), so a card that only SAYS it is blocked is refused.
  3. Neither may be fleet-buildable. A proof the fleet can close by merging its
     own code is not a proof — the whole value is that something other than the
     builder confirms it.
  4. Neither may wear a BUILD role (DRE-3039). `agent:engineer` on a proof card
     is a card the relay dispatches to a build agent the moment anything
     promotes it, and the thing that agent would build is the proof of its own
     siblings' work.

## And the check WRITES the verdict it computed (DRE-3039)

Rule 3 read a verdict off each card, printed a one-line summary and stamped
NOTHING — so `routing_verdict.promotion_refusal()` found no verdict on the
card, returned None ("a CHILD with NO verdict promotes exactly as it did
before"), and the sweep promoted the pair the moment their siblings reached
Done. A rule enforced at plan time and discarded before build time is not
enforced.

So `check` writes what it computed, as the same `🧭 routing-verdict` comment
every other verdict uses, through `routing_verdict.stamp_card` — one writer,
the one that already knows the answer, and no second grammar for the promotion
gate to learn. It writes only for a pair that PASSED: an epic on its way back
to Planning is not an epic whose cards get a routing decision written on them.
`--no-stamp` gives the pure read back, for a planner checking its own work
before it finishes.

## Rule 3 is READ from the vocabulary, not restated here

`config/routing-verdicts.json` already names, for every verdict, the actor
accountable for the card at its destination, and `planning_route.HUMAN_ACTORS`
already names which of those is a person. So the verdicts that may confirm an
epic are DERIVED — the ones a human acts on — which today is exactly WORKBENCH
and OPERATOR. Hand WORKBENCH to an agent in the file and it stops being a
confirmation, which is what "the builder does not confirm its own work" has to
mean mechanically. `vocabulary_problems()` refuses a file where nobody human is
left, or where a verdict the sweep promotes would count as a confirmation.

Rule 4 is read the same way, from both ends: the roles a BUILD run is
dispatched for come off `agents.yaml` (the roster entries that run on
`agent-task.yml`), and the role label the pair MAY wear comes off the routing
vocabulary (the `agent:*` labels mapped to a verdict a human acts on — today
`agent:ops`). Neither list is written down here, so a fifth build role or a
second operator label moves the rule with it.

Pure functions over card records, plus ONE write: the stamp above, which is a
Linear comment and the labels the verdict declares. Everything the plan gate
decides is decided by the pure half, so plan.yml, the scenario walk and the
tests all run the same code. The records come in on stdin from `linear_ops.py
children-detail`, the same shape-on-stdin contract `plan_critic.py mechanical`
uses.

CLI:

    python3 scripts/linear_ops.py children-detail DRE-N \\
      | python3 scripts/proof_and_demo.py check --epic DRE-N \\
          [--comment-file F] [--no-stamp]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
import planning_route  # noqa: E402
import routing_verdict  # noqa: E402

# The roster, and the field that says which agents a BUILD run is dispatched
# for. `agents.yaml` is the declared source of truth for "what agents exist and
# how they run" — reading it is what keeps rule 4 from being a list of four
# names that goes stale the day a fifth build role is registered.
ROSTER_PATH = os.path.join(ROOT, "agents.yaml")
BUILD_WORKFLOW = ".github/workflows/agent-task.yml"

# The two title conventions. Anchored at the START of the title, never a
# substring: `Record the demo: phase 3` is an ordinary code card, and reading
# it as a demo is the same mistake class as a substring blocker match. The
# DEMO: half is the pipeline's existing convention — the routing vocabulary
# routes it to WORKBENCH by title and `linear_ops.auto_done_skip_reason`
# refuses to auto-close it — and `tests/test_proof_and_demo.py` pins all three
# readers to the same answer rather than letting a fourth spelling appear.
PROOF_PREFIX = "PROOF:"
DEMO_PREFIX = "DEMO:"
_PROOF_TITLE = re.compile(r"^\s*proof:", re.IGNORECASE)
_DEMO_TITLE = re.compile(r"^\s*demo:", re.IGNORECASE)

# The marker the bounce comment opens with. Deliberately shares no prefix with
# a routing verdict, a planning shape or a merge-gate verdict: a note that
# looked like one of those would be read back as the decision it is describing
# (standards/untrusted-content.md).
BOUNCE_TAG = "epic-owes-proof-and-demo"
BOUNCE_MARK = "🧾"


def is_proof(title: str) -> bool:
    return bool(_PROOF_TITLE.match(title or ""))


def is_demo(title: str) -> bool:
    return bool(_DEMO_TITLE.match(title or ""))


# --------------------------------------------------------------------------- #
# which verdicts may confirm an epic                                           #
# --------------------------------------------------------------------------- #


def confirming_verdicts(doc: dict | None = None) -> tuple:
    """The verdicts a proof or demo card may carry: the ones whose accountable
    actor is a HUMAN.

    Derived from `config/routing-verdicts.json`, in the file's own order, so
    the rule moves when the file does. Today that is WORKBENCH and OPERATOR;
    FLEET's actor is `agent-task.yml`, which is the builder.
    """
    return tuple(
        name for name in routing_verdict.verdicts(doc)
        if routing_verdict.actor(name, doc) in planning_route.HUMAN_ACTORS
    )


def confirming_role_labels(doc: dict | None = None) -> tuple:
    """The role labels a proof or demo card MAY wear: the `agent:*` labels the
    routing vocabulary maps to a verdict a human acts on.

    Derived, for the same reason rule 3's verdicts are. Today that is exactly
    `agent:ops` — the label the vocabulary already reads as "a person handles
    this", read first, before any judgement. `no-code` maps to OPERATOR too and
    is deliberately not here: it says no code is produced, not who the card
    belongs to, and a card still owes a role label (`validate_card.missing`).
    """
    confirming = set(confirming_verdicts(doc))
    return tuple(
        label for label, verdict in routing_verdict.label_map(doc).items()
        if label.startswith("agent:") and verdict in confirming
    )


_ROSTER_CACHE: dict = {}


def build_roles(path: str | None = None) -> tuple:
    """The roles a BUILD run is dispatched for, read off the roster.

    `agents.yaml` already declares, per agent, the workflow that implements it;
    the ones running on `agent-task.yml` are the ones a card's role label sends
    a build agent at. Today: engineer, frontend, devops, database-architect.

    Raises rather than degrading. A roster this cannot read is a rule 4 that
    cannot be evaluated, and a guard that quietly disappears when its source is
    unreadable is worse than no guard: the plan run fails loudly instead, which
    is the only outcome that gets looked at (standards/console-honesty.md
    rule 1 — a crashed read decided nothing).
    """
    path = path or ROSTER_PATH
    if path not in _ROSTER_CACHE:
        import yaml  # lazy: PyYAML ships in the runner image, the rest is stdlib

        with open(path, encoding="utf-8") as fh:
            roster = yaml.safe_load(fh) or {}
        names = tuple(
            (entry.get("name") or "").strip().lower()
            for entry in (roster.get("agents") or ())
            if (entry.get("workflow") or "").strip() == BUILD_WORKFLOW
        )
        if not names:
            raise RuntimeError(
                f"{path} names no agent running on {BUILD_WORKFLOW} — rule 4 has "
                "no build roles to refuse, so a proof card wearing one would "
                "pass unread"
            )
        _ROSTER_CACHE[path] = names
    return _ROSTER_CACHE[path]


def vocabulary_problems(doc: dict | None = None) -> list:
    """Everything wrong with the vocabulary as a source for rules 3 and 4, or
    an empty list."""
    problems: list[str] = []
    confirming = confirming_verdicts(doc)
    if not confirming_role_labels(doc):
        problems.append(
            "no `agent:*` label maps to a verdict a human acts on, so the check "
            "can refuse a proof or demo card's build role without being able to "
            "name the label it should carry instead — a refusal with no remedy"
        )
    if not confirming:
        problems.append(
            "no routing verdict names a human as its accountable actor, so "
            "nothing in the vocabulary can confirm an epic — a proof the fleet "
            "closes by merging its own code is not a proof"
        )
    for name in confirming:
        if routing_verdict.is_promotable(name, doc):
            problems.append(
                f"the verdict {name!r} names a human actor AND is promoted by "
                "the sweep — a card the fleet may build cannot be the thing "
                "that confirms the fleet's work"
            )
    return problems


# --------------------------------------------------------------------------- #
# reading one epic's children                                                  #
# --------------------------------------------------------------------------- #


def _ident(card: dict) -> str:
    return (card or {}).get("identifier") or "(unidentified card)"


def _verdict(card: dict, doc: dict | None = None):
    decision = routing_verdict.route(
        card.get("title") or "", card.get("body") or "",
        card.get("labels") or (), doc=doc,
    )
    return decision.verdict, decision.reason


def _verdict_finding(card: dict, kind: str, doc: dict | None = None) -> str | None:
    """Why this card's verdict disqualifies it from confirming the epic."""
    verdict, reason = _verdict(card, doc)
    allowed = confirming_verdicts(doc)
    if verdict in allowed:
        return None
    named = " or ".join(allowed) if allowed else "a verdict a human acts on"
    if verdict == "FLEET":
        return (
            f"{_ident(card)}: the {kind} card routes FLEET — {reason}. A "
            "proof the fleet can close by merging its own code is not a "
            f"proof; write criteria that route it to {named}."
        )
    if verdict is None:
        return (
            f"{_ident(card)}: the {kind} card routes to no verdict — {reason} "
            f"A {kind} card must land on {named}, so name the live "
            "observation (or the operator's hand) in its acceptance criteria."
        )
    return (
        f"{_ident(card)}: the {kind} card routes {verdict} — {reason} It must "
        f"carry {named}, because the whole value is that something other than "
        "the builder confirms it."
    )


def _role_of(label: str) -> str | None:
    """The role an `agent:<role>` label names, or None. An EXACT prefix split,
    never a substring: `agent:engineering-manager` is not `agent:engineer`."""
    text = (label or "").strip().lower()
    if not text.startswith("agent:"):
        return None
    return text.split(":", 1)[1].strip()


def _role_label_finding(card: dict, kind: str, doc: dict | None = None) -> str | None:
    """Why this card's ROLE LABEL disqualifies it from confirming the epic.

    The verdict is what the promotion gate reads, and the role label is what
    the relay reads. DRE-3031 carried `agent:engineer` and a `Files:` line
    naming the document it was to write: had anything promoted it, an engineer
    agent would have written the proof of its own siblings' work.
    """
    build = build_roles()
    worn = [
        label for label in (card.get("labels") or ())
        if _role_of(label) in build
    ]
    if not worn:
        return None
    allowed = confirming_role_labels(doc)
    named = " or ".join(f"`{l}`" for l in allowed) if allowed else \
        "a role label the vocabulary routes to a human"
    return (
        f"{_ident(card)}: the {kind} card carries "
        + ", ".join(f"`{l}`" for l in worn)
        + " — a role a build run is dispatched for, so the fleet is what picks "
        "it up. The pair is confirmed by a person: create it with "
        f"--label {allowed[0] if allowed else 'agent:ops'} and drop the "
        "inherited role (`linear_ops.py remove-label <CARD> "
        f"{worn[0]}`), so the card wears {named}."
    )


def findings(children: list, doc: dict | None = None) -> list:
    """Everything wrong with this epic's proof/demo pair, or an empty list.

    `children` are the epic's cards IN CREATION ORDER, each a record from
    `linear_ops.py children-detail`: `identifier`, `title`, `body`, `labels`,
    `blocked_by` (formal `blocks` relations only).
    """
    cards = list(children or [])
    found: list[str] = []

    proofs = [c for c in cards if is_proof(c.get("title"))]
    demos = [c for c in cards if is_demo(c.get("title"))]

    for kind, prefix, matched in (
        ("proof", PROOF_PREFIX, proofs), ("demo", DEMO_PREFIX, demos)
    ):
        if not matched:
            found.append(
                f"no {kind} card: no child's title opens `{prefix}`. Every "
                f"epic carries one, as one of its last two children."
            )
        elif len(matched) > 1:
            found.append(
                f"{len(matched)} {kind} cards — "
                + ", ".join(_ident(c) for c in matched)
                + f". Exactly one `{prefix}` card per epic; picking between "
                "two would be inventing the decision rather than reading it."
            )

    if len(proofs) != 1 or len(demos) != 1:
        return found

    proof, demo = proofs[0], demos[0]
    pair = {_ident(proof), _ident(demo)}

    # 1 — the last two children.
    last_two = {_ident(c) for c in cards[-2:]}
    if pair != last_two:
        found.append(
            f"{_ident(proof)} and {_ident(demo)} are not the last two "
            "children — the last two are " + ", ".join(sorted(last_two))
            + ". The pair closes the epic, so it is emitted last."
        )

    # 2 — blocked by every other child, by RELATION.
    siblings = [_ident(c) for c in cards if _ident(c) not in pair]
    for kind, card in (("proof", proof), ("demo", demo)):
        missing = [s for s in siblings if s not in (card.get("blocked_by") or [])]
        if missing:
            found.append(
                f"{_ident(card)}: the {kind} card is not blocked by "
                + ", ".join(missing)
                + " — `blocked by every sibling` is a Linear `blocks` "
                "relation, not an ordering and not a `**Blocked by:**` line. "
                "The relation is what the reconcile gates honour."
            )
    if _ident(demo) in (proof.get("blocked_by") or []) and \
            _ident(proof) in (demo.get("blocked_by") or []):
        found.append(
            f"{_ident(proof)} and {_ident(demo)} block each other — that is a "
            "deadlock, not an order. Either may wait on the other; not both."
        )

    # 3 — neither is fleet-buildable.
    for kind, card in (("proof", proof), ("demo", demo)):
        problem = _verdict_finding(card, kind, doc)
        if problem:
            found.append(problem)

    # 4 — neither wears a build role.
    for kind, card in (("proof", proof), ("demo", demo)):
        problem = _role_label_finding(card, kind, doc)
        if problem:
            found.append(problem)

    return found


# --------------------------------------------------------------------------- #
# the stamp — what the check computed, written where the sweep reads it        #
# --------------------------------------------------------------------------- #


def stamps(children: list, doc: dict | None = None) -> tuple:
    """`(identifier, verdict, why)` for the epic's proof and demo cards.

    Empty for a pair with any finding against it: an epic on its way back to
    Planning is not an epic whose cards get a routing decision written on them,
    and the cards may not survive the re-plan at all.
    """
    cards = list(children or [])
    if findings(cards, doc):
        return ()
    out: list[tuple[str, str, str]] = []
    for kind, matches in (("proof", is_proof), ("demo", is_demo)):
        card = next((c for c in cards if matches(c.get("title"))), None)
        if card is None:  # pragma: no cover — findings() already refused this
            return ()
        verdict, reason = _verdict(card, doc)
        out.append((
            _ident(card),
            verdict,
            f"the epic's {kind} card, routed by the proof-and-demo check off "
            f"the card itself: {reason} An epic is confirmed by something other "
            "than its builder, so the fleet is never sent at this card.",
        ))
    return tuple(out)


def write_stamps(children: list, doc: dict | None = None) -> int:
    """Write each computed verdict onto its card. Returns how many were written.

    `routing_verdict.stamp_card` is the whole write — the comment and the marks
    the verdict declares, one implementation for both callers. It refuses a
    card that already carries a verdict (a re-planned epic runs this check
    again) and says so on stderr rather than raising: the pair is well-formed
    either way, and the gate that actually holds the cards is
    `promotion_refusal`, which refuses a card carrying two verdicts as loudly
    as it refuses one that is not FLEET.

    A failed WRITE is different and does propagate: the plan step's `if !`
    branch then finds no bounce note and fails the run without moving the epic,
    which is the honest outcome — a Linear write that did not land decided
    nothing about this plan (standards/console-honesty.md rule 1).
    """
    return sum(
        1 for identifier, verdict, why in stamps(children, doc)
        if routing_verdict.stamp_card(identifier, verdict, why) == 0
    )


def bounce_comment(epic: str, found: list) -> str:
    """The note posted to the epic when the pair is missing or malformed.

    Raises on an empty finding list: a bounce with nothing to say is a plan
    stopped for no stated reason, which is the failure this card exists to
    prevent one level up.
    """
    if not found:
        raise ValueError(
            "refusing to write a bounce with no finding — an epic is only sent "
            "back with the reason named"
        )
    lines = [
        f"{BOUNCE_MARK} {BOUNCE_TAG}: {epic} is back in **Planning** — it owes "
        "a proof card and a demo card.",
        "",
        "**Proof** answers *did it work*, and it is not a green test suite: it "
        "is the mechanism observed running against real state, with the "
        "observation recorded in the repo. **Demo** answers *can the CEO see "
        "it* — a merged pull request is invisible to the person who green-lit "
        "the epic.",
        "",
        "Both are the epic's **last two children**, both are **blocked by "
        f"every other child** (the Linear relation, not a body line), and "
        "neither may be fleet-buildable — "
        + " or ".join(confirming_verdicts())
        + " only, because the whole value is that something other than the "
        "builder confirms it.",
        "",
        "**What is missing:**",
        "",
    ]
    lines += [f"- {f}" for f in found]
    lines += [
        "",
        "Re-plan this epic with the two cards added and it moves on.",
    ]
    return "\n".join(lines)


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
    found = findings(children)
    if not found:
        proofs = [_ident(c) for c in children if is_proof(c.get("title"))]
        demos = [_ident(c) for c in children if is_demo(c.get("title"))]
        print(
            f"{args.epic}: {len(children)} card(s) — proof {', '.join(proofs)}, "
            f"demo {', '.join(demos)}, both last and blocked by every sibling"
        )
        # ...and the verdict it computed goes ON the cards, because the sweep
        # reads the card, not this run's log (DRE-3039).
        if args.stamp:
            print(f"{args.epic}: {write_stamps(children)} verdict(s) stamped")
        return 0
    for finding in found:
        print(finding)
    if args.comment_file:
        with open(args.comment_file, "w", encoding="utf-8") as fh:
            fh.write(bounce_comment(args.epic, found))
    return 1


def _cmd_vocabulary(_args) -> int:
    problems = vocabulary_problems()
    for problem in problems:
        print(f"  [FAIL] {problem}")
    print(
        f"{len(confirming_verdicts())} confirming verdict(s) — "
        + ", ".join(confirming_verdicts())
        + f"; {len(problems)} problem(s)"
    )
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="the epic's cards, on stdin")
    check.add_argument("--epic", required=True)
    check.add_argument("--comment-file", default=None,
                       help="where to write the bounce note, when there is one")
    check.add_argument("--no-stamp", dest="stamp", action="store_false",
                       default=True,
                       help="read only — do not write the verdict onto the pair")
    check.set_defaults(fn=_cmd_check)

    vocab = sub.add_parser("vocabulary", help="validate the derived rule")
    vocab.set_defaults(fn=_cmd_vocabulary)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_usage(sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
