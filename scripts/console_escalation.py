#!/usr/bin/env python3
"""The three Green Light lines — Finding, Question, Recommendation (DRE-3908).

Every decision-kind Green Light park carries three machine-readable lines, in
this order, each on its own line:

    🔎 Finding: <the specific one-line reason, in plain words>
    ❓ Question: <the one question the CEO answers>
    💡 Recommendation: <recommended answer> — <one-line why>

When nothing was recommended the third line says so — `💡 Recommendation: none
given — <why not>` — and is never left out. This module declares the prefixes
and the `none given` grammar ONCE: it renders an escalation into the lines,
parses them back out of a comment, lifts them out of a reason, names what is
wrong with a body, and completes a free-prose question that declared none of
them. No other file restates the strings. The writers built on it import this
module (DRE-3909, DRE-3910, DRE-5204, DRE-3911, DRE-6174, and DRE-6189 in the
next epic), the shell tier runs its CLI, and the one reader in this repository
(DRE-6196) parses through it too.

## Who reads the lines

On the day this shipped nothing did. Once the writers land the readers are
exactly two: a person reading the card in Linear, and the hygiene agent's Green
Light lane once DRE-6196 teaches it to parse the whole note through this module
— today's one pipeline reader. The console card for these lines, DRE-3894, was
canceled on 2026-10-02 in the stale-card cleanup; a console reader is to be
re-filed under a live epic when one is wanted, and until then no card claims
the console parses these lines. When it is filed it mirrors these exact
strings under the two-copies rule, and from then on a change here is a change
in two repos.

## The choices are not a new grammar

The pick-one answers are the `escalation-choices` fenced JSON block DRE-6168
built for the planner's own escalations, whose contract is
`docs/escalation-choices.md` and whose validator, writer and reader live in
`planning_escalation`. This module builds that block from the same
`Escalation` the three lines come from, and the Recommendation line's answer is
the recommended choice's own `label` — so a rendered body can never recommend
one thing in prose while the console highlights another. The fence name, the
validator, the writer and the reader are all imported, never restated, and the
import happens inside the functions that need it: `planning_escalation` imports
`plan_critic` at module level, and `plan_critic` will import this module
(DRE-3910). A body with no fence never imports it at all, so the CLI the shell
tier runs needs nothing beyond the standard library.

Run: python3 scripts/console_escalation.py render --finding "…" --question "…" \\
         [--recommendation "…" --why "…"]
     python3 scripts/console_escalation.py complete <file> --question "…" --who "…"
     python3 scripts/console_escalation.py check <file>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

#: The synthetic cases this epic was written from — inspiration, not records.
FIXTURES = os.path.join(ROOT, "tests", "fixtures",
                        "green-light-escalations-2026-09-14.json")

FINDING_PREFIX = "🔎 Finding:"
QUESTION_PREFIX = "❓ Question:"
RECOMMENDATION_PREFIX = "💡 Recommendation:"
NONE_GIVEN = "none given"

#: Between the recommended answer and its why.
SEPARATOR = " — "

#: The three lines, in the order a body carries them, with the name a problem
#: report gives each.
_LINES = (("Finding", FINDING_PREFIX), ("Question", QUESTION_PREFIX),
          ("Recommendation", RECOMMENDATION_PREFIX))

#: What the CLI's `render` says when no reason for a missing recommendation
#: was given either.
NO_RECOMMENDATION_STATED = "no recommendation was stated"

#: The `plan_critic.one_line` bound on a derived Finding.
ONE_LINE_LIMIT = 300

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")

#: What an em dash inside a free answer becomes, because the first em dash on
#: the Recommendation line is where its why begins. A choice's label keeps its
#: em dash: the block names the label, so the reader finds where it ends.
_ANSWER_DASH = "–"


@dataclass(frozen=True)
class Choice:
    """One button: the `escalation-choices` choice keys, `preview` optional."""

    id: str
    label: str
    effect: str
    outcome: str
    preview: str = ""


@dataclass(frozen=True)
class Escalation:
    """What a Green Light park says. `recommended` is a choice `id`; empty
    means the first choice."""

    finding: str
    question: str
    recommendation: str | None
    why: str
    choices: tuple = ()
    recommended: str = ""


# --------------------------------------------------------------------------- #
# writing                                                                      #
# --------------------------------------------------------------------------- #


def _recommended_choice(esc: Escalation) -> Choice | None:
    if not esc.choices:
        return None
    wanted = esc.recommended or esc.choices[0].id
    return next((c for c in esc.choices if c.id == wanted), None)


def _flat(text: str) -> str:
    """`text` on one line: every run of whitespace, newlines included, is one
    space, so no value can split a line or open one of its own."""
    return " ".join(str(text).split())


def render(esc: Escalation) -> str:
    """The three lines. With choices the answer is the recommended choice's
    label and `esc.recommendation` is not used for it. Every value is flattened
    onto its line, and an em dash in a free answer becomes an en dash, so what
    this writes `problems` accepts and `parse` reads back."""
    choice = _recommended_choice(esc)
    if choice:
        answer = _flat(choice.label)
    elif esc.recommendation is None:
        answer = NONE_GIVEN
    else:
        answer = _flat(esc.recommendation).replace(SEPARATOR.strip(),
                                                   _ANSWER_DASH)
    return "\n".join((
        f"{FINDING_PREFIX} {_flat(esc.finding)}",
        f"{QUESTION_PREFIX} {_flat(esc.question)}",
        f"{RECOMMENDATION_PREFIX} {answer}{SEPARATOR}{_flat(esc.why)}",
    ))


def block(esc: Escalation) -> dict | None:
    """The `escalation-choices` block for `esc`, or None when it has no
    choices or the block's validator refuses it — logged to stderr in the
    words `planning_escalation.read_choices` uses. The question, context, why
    and labels are flattened exactly as `render` flattens them, so the block
    and the lines say the same words."""
    if not esc.choices:
        return None
    import planning_escalation

    choices = []
    for c in esc.choices:
        item = {"id": c.id, "label": _flat(c.label), "effect": c.effect}
        if c.preview:
            item["preview"] = c.preview
        item["outcome"] = c.outcome
        choices.append(item)
    built = {
        "question": _flat(esc.question),
        "context": _flat(esc.finding),
        "choices": choices,
        "recommended": esc.recommended or esc.choices[0].id,
        "why": _flat(esc.why),
    }
    problem = planning_escalation.choices_problem(built)
    if problem:
        print(f"{planning_escalation.CHOICES_FENCE} refused: {problem}",
              file=sys.stderr)
        return None
    return built


def render_with_block(esc: Escalation) -> str:
    """The three lines, then the block when there is one — always last."""
    text = render(esc)
    built = block(esc)
    if built is None:
        return text
    import planning_escalation

    return f"{text}\n\n{planning_escalation.choices_block(built)}"


# --------------------------------------------------------------------------- #
# reading                                                                      #
# --------------------------------------------------------------------------- #


def _first_lines(lines: list[str]) -> dict:
    """`{prefix: index}` of the first line opening with each prefix."""
    found: dict = {}
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        for _, prefix in _LINES:
            if prefix not in found and stripped.startswith(prefix):
                found[prefix] = i
    return found


def _value(line: str, prefix: str) -> str:
    return line.lstrip()[len(prefix):].strip()


def _recommendation(value: str,
                    label: str | None = None) -> tuple[str | None, str, bool]:
    """`(answer, why, has_separator)` from a Recommendation line's value;
    the answer is None for `none given`. Given the recommended choice's
    `label`, a value that opens with it is read as that answer even when the
    label carries an em dash; otherwise the answer ends at the first one."""
    if label and (value == label or value.startswith(label + SEPARATOR)):
        why = value[len(label):]
        return label, why.strip()[len(SEPARATOR.strip()):].strip(), bool(why)
    head, sep, why = value.partition(SEPARATOR.strip())
    answer = head.strip()
    return (None if answer == NONE_GIVEN else answer), why.strip(), bool(sep)


def _blocks(text: str) -> list:
    """`[(match, accepted block or None, refusal or None)]` for every
    `escalation-choices` block, in document order. The fence pattern is the
    one `parse_choices` reads with, so a block `parse` reads is exactly the
    block `split` lifts. Empty — and nothing imported — when the text carries
    no fence at all."""
    if "```" not in text:
        return []
    import plan_artifact
    import planning_escalation

    found = []
    for match in plan_artifact._FENCE.finditer(text):
        if match.group("lang").lower() != planning_escalation.CHOICES_FENCE:
            continue
        data = planning_escalation.parse_choices(match.group(0))
        problem = (planning_escalation.choices_problem(data) if data is not None
                   else "the block is not a JSON object")
        found.append((match, None if problem else data, problem))
    return found


def _accepted(blocks: list):
    """The last block the validator accepts, as `(match, data)`, or None."""
    for match, data, problem in reversed(blocks):
        if problem is None:
            return match, data
    return None


def _recommended_label(accepted) -> str | None:
    """The recommended choice's label in an accepted block, or None."""
    if not accepted:
        return None
    data = accepted[1]
    return next(c["label"] for c in data["choices"]
                if c["id"] == data["recommended"])


def _choices_from(data: dict) -> tuple:
    return tuple(Choice(c["id"], c["label"], c["effect"], c["outcome"],
                        c.get("preview", "")) for c in data["choices"])


def parse(text: str) -> Escalation | None:
    """What a body's lines and its last accepted block say, or None unless
    all three lines are present."""
    lines = text.split("\n")
    at = _first_lines(lines)
    if len(at) < len(_LINES):
        return None
    accepted = _accepted(_blocks(text))
    answer, why, _ = _recommendation(
        _value(lines[at[RECOMMENDATION_PREFIX]], RECOMMENDATION_PREFIX),
        _recommended_label(accepted))
    choices, recommended = (), ""
    if accepted:
        choices = _choices_from(accepted[1])
        recommended = accepted[1]["recommended"]
    return Escalation(
        finding=_value(lines[at[FINDING_PREFIX]], FINDING_PREFIX),
        question=_value(lines[at[QUESTION_PREFIX]], QUESTION_PREFIX),
        recommendation=answer,
        why=why,
        choices=choices,
        recommended=recommended,
    )


def split(text: str) -> tuple[str, Escalation | None]:
    """The text with the three lines and the one accepted block lifted out,
    and what they said. A refused block stays, so the reason's own check still
    refuses it as a code fence; a body without all three lines lifts nothing."""
    esc = parse(text)
    if esc is None:
        return text, None
    lines = text.split("\n")
    drop = set(_first_lines(lines).values())
    accepted = _accepted(_blocks(text))
    if accepted:
        match = accepted[0]
        first = text.count("\n", 0, match.start())
        last = text.count("\n", 0, match.end() - 1)
        drop.update(range(first, last + 1))
    kept: list[str] = []
    lifted = False
    for i, line in enumerate(lines):
        if i in drop:
            lifted = True
            continue
        # A blank line on each side of what was lifted collapses to one.
        if lifted and not line.strip() and (not kept or not kept[-1].strip()):
            continue
        lifted = False
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    while kept and not kept[0].strip():
        kept.pop(0)
    return "\n".join(kept), esc


def problems(text: str) -> list[str]:
    """Every way `text` falls short of the contract, named. Empty means it
    conforms; three lines and no block conform."""
    found: list[str] = []
    lines = text.split("\n")
    at = _first_lines(lines)
    for name, prefix in _LINES:
        if prefix in at:
            continue
        if any(prefix in line for line in lines):
            found.append(f"the {name} line is not on its own line")
        else:
            found.append(f"the {name} line ({prefix}) is missing")
    order = [at[prefix] for _, prefix in _LINES if prefix in at]
    if order != sorted(order):
        found.append("the lines are out of order — Finding, then Question, "
                     "then Recommendation")
    for name, prefix in _LINES:
        if prefix in at and not _value(lines[at[prefix]], prefix):
            found.append(f"the {name} line is empty")
    blocks = _blocks(text)
    accepted = _accepted(blocks)
    label = _recommended_label(accepted)
    answer = None
    if RECOMMENDATION_PREFIX in at:
        value = _value(lines[at[RECOMMENDATION_PREFIX]], RECOMMENDATION_PREFIX)
        answer, why, has_sep = _recommendation(value, label)
        if value and answer is None and not why:
            found.append(f"the Recommendation says {NONE_GIVEN} with no why "
                         f"({NONE_GIVEN}{SEPARATOR}<why not>)")
        elif value and answer is not None and not why:
            found.append(f"the Recommendation has no{SEPARATOR}<why> clause")
        elif value and has_sep and answer == "":
            found.append("the Recommendation line gives a why and no answer")
    if blocks:
        import planning_escalation

        fence = planning_escalation.CHOICES_FENCE
        if len(blocks) > 1:
            found.append(f"the body carries more than one {fence} block "
                         f"({len(blocks)}); it may carry one")
        for _, _, problem in blocks:
            if problem:
                found.append(f"the {fence} block is refused: {problem}")
    if accepted:
        data = accepted[1]
        if RECOMMENDATION_PREFIX in at and answer != label:
            found.append(f"the Recommendation answer {answer!r} is not the "
                         f"recommended choice's label {label!r}")
        if QUESTION_PREFIX in at and data["question"] != _value(
                lines[at[QUESTION_PREFIX]], QUESTION_PREFIX):
            found.append("the block's question differs from the Question line")
    return found


# --------------------------------------------------------------------------- #
# completing a free-prose question                                             #
# --------------------------------------------------------------------------- #


def _one_line(text: str, limit: int = ONE_LINE_LIMIT) -> str:
    """`plan_critic.one_line`'s rule, kept here so the CLI imports nothing
    beyond the standard library."""
    flat = _flat(text)
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    return _one_line(_SENTENCE_END.split(flat, maxsplit=1)[0])


def complete(text: str, *, question: str, who: str,
             finding: str | None = None) -> str:
    """`text` unchanged when it declares all three lines; otherwise `text`, a
    blank line and the three lines, saying `who` recommended nothing. Never
    adds a block."""
    if parse(text) is not None:
        return text
    asked = [line.strip() for line in text.split("\n")
             if line.strip().endswith("?")]
    esc = Escalation(
        finding=finding or _first_sentence(text) or f"{who} stated no finding",
        question=asked[-1] if asked else question,
        recommendation=None,
        why=f"{who} stated no recommendation",
    )
    body = text.rstrip()
    return f"{body}\n\n{render(esc)}" if body else render(esc)


def load_fixtures() -> list[dict]:
    """The four synthetic cases of 2026-09-14."""
    with open(FIXTURES, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        print(f"console_escalation: cannot read {path}: {exc}", file=sys.stderr)
        return None


def _cmd_render(args) -> int:
    if (args.recommendation or "").strip():
        if not (args.why or "").strip():
            print("console_escalation: --recommendation needs --why",
                  file=sys.stderr)
            return 2
        esc = Escalation(args.finding, args.question, args.recommendation,
                         args.why)
    else:
        esc = Escalation(args.finding, args.question, None,
                         (args.why or "").strip() or NO_RECOMMENDATION_STATED)
    print(render(esc))
    return 0


def _cmd_complete(args) -> int:
    text = _read(args.file)
    if text is None:
        return 2
    print(complete(text, question=args.question, who=args.who,
                   finding=args.finding))
    return 0


def _cmd_check(args) -> int:
    text = _read(args.file)
    if text is None:
        return 2
    found = problems(text)
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("render", help="print the three lines")
    p.add_argument("--finding", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--recommendation", default="")
    p.add_argument("--why", default="")
    p.set_defaults(func=_cmd_render)

    p = sub.add_parser("complete",
                       help="append the three lines to a body that lacks them")
    p.add_argument("file")
    p.add_argument("--question", required=True)
    p.add_argument("--who", required=True)
    p.add_argument("--finding", default=None)
    p.set_defaults(func=_cmd_complete)

    p = sub.add_parser("check", help="exit 1 naming every defect on stderr")
    p.add_argument("file")
    p.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
