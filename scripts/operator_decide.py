#!/usr/bin/env python3
"""The operator agent's decision (DRE-8744, epic DRE-8738).

A card waiting on a person gets at most one outcome per pass — `do`, `studio`,
`ceo` or `done-already` — and a card the model has not answered gets none: it
is `unread` on that pass, has no write and is never counted as `do`. This
module picks the outcome from the fixed routes of `operator_rules.py`
(DRE-8743) and the model's answer, and it guards that answer so a model can
never put a non-decision in front of the CEO. It also writes a decision down
as a receipt and reads it back, so no sibling restates the format.

    read_answer(path, group=WAITING_GROUP) -> dict | None
    decide(card, answer, card_text, as_built=None, rules=None, choices_file=None,
           group=WAITING_GROUP) -> Decision | None
    three_lines(decision) -> str
    fingerprint(card) -> str
    receipt_text(decision, now) -> str
    decision_from_receipt(row, body) -> Decision | None
    newest_receipt(records) -> dict | None
    keep_stands(records, fingerprint) -> bool
    guard_returned(records, receipt) -> bool
    log_line(decision, action="decide") -> str
    unread_line(card, why) -> str
    do_claim_text(decision, command_lines, now) -> str
    do_receipt_text(decision, transcript, ending, now) -> str
    do_close_failed_text(decision, what, now) -> str
    do_ending_from_receipt(body) -> dict | None
    do_commands_from_receipt(body) -> list | None
    do_decision_from_receipt(row, record) -> Decision | None

## The answer

One JSON file per card, `<answers dir>/<CARD>.json`, with the keys
`ANSWER_KEYS`. A missing, unreadable or malformed file, an `outcome` outside
`OUTCOMES` and an empty `step` are all read as no answer, never as an error. A
business choice's choices are a file of their own, `<CARD>.choices.json`, in
the planner-escalation-choices shape, because `planning_escalation.screen`
screens a FILE, never a dict.

## The five rules, in order — the first that holds decides

1. `done-already`: the answer says so, with at least one `evidence` quote, and
   every quote occurs in `card_text`, whitespace collapsed. A card whose close
   only a person or another run makes (`CLOSES`) is `do` instead, with the
   step "close the card through <that close>". This comes before the CEO's
   kept acts: closing a card whose criteria are met performs no act.
2. `ceo` by rule: `operator_rules.rule_outcome` answers `ceo`. Its `choices`
   is `None` — the kept list is this card's screen.
3. `ceo` as a business choice: the answer says `ceo`, and
   `planning_escalation.screen(three_lines(...), choices_file, writer=WRITER)`
   passes the choices file. `choices` is `read_choices(choices_file)`.
4. `studio`: the rule answers `studio`, or the answer says `studio` with a
   non-empty `access`. `host_cannot` keeps only the quotes that occur in
   `as_built` — `operator_rules.host_limits_text()`, this repository's copy of
   the studio host's limits. The decision is held, and nothing is written for
   it, when it is the studio go-live card itself, when `as_built` was not
   read, or when no quote is kept (`HELD_*`).
5. `do`: everything else, with the answer's `step`.

So the rules never decide a card alone, and a card's step is always the
model's. For `ceo`, the Finding, Question and Recommendation are the answer's
when all three pass `planning_escalation.refusal` and the question holds
exactly one question mark — otherwise they are written from the rule, and the
Recommendation reads `none given — <reason>`.

## A row in Green Light (DRE-9047)

A row read with `group=GREEN_LIGHT_GROUP` gets exactly one of
`GREEN_LIGHT_OUTCOMES`, or none:

* `keep` — a genuine decision, and it stays with the CEO. It is rules 2 and 3
  above run live, the same code in the same order: `rule_outcome` answers
  `ceo`, or the answer says `ceo` and the screen passes its choices file. The
  kept act is `act`; nothing else is carried, because the card already holds
  its question. A model `ceo` the screen refuses is no decision.
* `repair` — a stopped run, sent back to `GREEN_LIGHT_DESTINATION`, whose own
  run re-does the work: a non-empty `step` (what stopped) and at least one
  `evidence` item.
* `answer` — a question the facts answer, sent back the same way: a
  non-empty `step` (the question), a non-empty `answer`, carried as the
  decision's `finding`, and at least one `evidence` item.

Every evidence item must verify: a quote that occurs in `card_text`, or a
`{"path", "quote"}` object whose quote occurs in that file of the checkout,
whitespace collapsed either way, and at most `MAX_PATH_EVIDENCE` of those. A
`path` is the model's view, `CHECKOUT_DIR/<path>`, or the bare `<path>`; one
prefix is removed, and the rest must be relative, hold no `..` part and
resolve, symlinks followed, to a regular file inside `ROOT`. The decision
and its receipt carry the bare form. `do`, `studio` and `done-already`, a
failed guard and a missing answer are no decision. A waiting row never gets a
`GREEN_LIGHT_OUTCOMES` outcome. `keep_stands` is the one reading of a
standing `keep`: the newest pipeline receipt is a `keep` over the card's
fingerprint and nothing of any author follows it.

## The fingerprint

The first 12 hex characters of a SHA-256 over the title and the description.
No lane and no label: labels are written by other agents and seams, and a
label added after a receipt tells the agent nothing new about the step.

## The receipt

    🛠️ operator-agent: <outcome> — <reason> · <HH:MM> PT
    fingerprint: <12 hex>
    step: <step>

then, for `studio`, one `as built, the studio host cannot: "<quote>"` line per
kept quote and `waits on: DRE-8582` (`, DRE-8487` for a deploy step); for
`ceo`, `act: <act id>` and, for a business choice, `choices: <one-line
JSON>`; for `done-already`, one `evidence: "<quote>"` line per quote and
`close: done`; for `repair` and `answer`, an `answer: <answer>` line (an
`answer` only), one evidence line per item — `evidence: <path>: "<quote>"`
for a file — and `MOVED_LINE`; for `keep`, `act: <act id>` and
`STAYS_LINE`. No line opens `Outcome:` or `Released as`: a `done-already`
close moves the card to `Done` knowing the relay's lane guard (DRE-7327) may
return it, and never forges a record to get past that guard. A comment opening
`GUARD_RETURN_TAG` after the receipt is `guard_returned`, and the agent writes
nothing more on that card.

## A `do` card's three comments (DRE-8923)

A `do` step runs commands that do not repeat safely, so a pass posts a claim,
and the claim lands, before the first one runs:

    🛠️ operator-agent: do-claim — <reason> · <HH:MM> PT
    fingerprint: <12 hex>
    step: <step>
    will run: `<command line>`        (one per command, in order)
    `CLAIM_LAST`

and the receipt after: the same three lines with `do`, a `pull request:
<url>` line when the transcript names one, then per command run a
`command:`, an `exit:` and an `output:` line and the output in a fence, then
— on a `failed:` ending only — one `left changed:` line per write that exited
0 before the failure (`left changed: nothing` when none did), and one ending
line: `close: <rule>`, `NONE_NAMED`, `failed: `<command>` — exit <n>; ...`,
`NO_TRANSCRIPT`, or `continues: after <YYYY-MM-DDTHH:MMZ> — <what remains>`.
Nothing is undone: an undo is another unreviewed write, so whoever picks the
card up — the CEO or its owner — reads exactly what still stands. A run that
wrote no transcript ran commands nobody can name, so its receipt carries no
`left changed:` line: the claim above it lists what may have run.

A close that can never land gets one more, `do-close`, with the fingerprint
and `failed: <what> — the card is left where it was`. An output is cut at
`OUTPUT_CHARS`, with `… cut, <n> characters` (the characters left out) after
its fence; a value of `SECRET_ENV` in any line reads `TOKEN_MARK`; and an
output line that would open `Outcome:` or `Released as` is written after
`RECORD_GUARD`. `decision_from_receipt` answers None for all three words, so
the agent reads them with `do_ending_from_receipt` and never part 1's rules.
Each is posted through the act registry (DRE-8925), so it ends with the
registry's trailer line, and every reader here sets that line aside.

## The log lines

Every Operator Agent log line opens with `LOG_TAG`, declared in
`config/proof-reach.json`'s `log_lines`:

    operator-agent: <CARD> → <outcome> — <reason> · step: <step>[ · held: <why>][ · <action>]
    operator-agent: <CARD> unread — <why>

Pure: it runs no model, reads no Linear and writes nothing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import console_escalation  # noqa: E402
import operator_rules  # noqa: E402
import pipeline_act  # noqa: E402 — the act trailer a posted `do` comment ends with
import planning_escalation  # noqa: E402
from operator_step_kind import _APOSTROPHES  # noqa: E402

OUTCOMES = ("do", "studio", "ceo", "done-already")
DO, STUDIO, CEO, DONE_ALREADY = OUTCOMES

#: A `Green Light` row's three outcomes (DRE-9047), and the two that move it.
REPAIR, ANSWER, KEEP = "repair", "answer", "keep"
GREEN_LIGHT_OUTCOMES = (REPAIR, ANSWER, KEEP)
MOVING_OUTCOMES = (REPAIR, ANSWER)

#: The two groups of row a pass reads: a card waiting on a person, and a row
#: in `Green Light`.
WAITING_GROUP, GREEN_LIGHT_GROUP = "waiting", "green-light"
GROUPS = (WAITING_GROUP, GREEN_LIGHT_GROUP)

#: The keys of a model's answer file. A choices block is never one of them.
ANSWER_KEYS = ("card", "outcome", "step", "reason", "evidence", "access", "deploy",
               "host_cannot", "finding", "question", "recommendation", "answer")
_ANSWER_TEXT = ("card", "step", "reason", "access", "finding", "question",
                "recommendation")
_ANSWER_LISTS = ("evidence", "host_cannot")

#: The most `{"path", "quote"}` evidence items one answer may carry: the
#: workflow's turn ceiling is sized against it.
MAX_PATH_EVIDENCE = 3
#: The bureau-pipeline checkout a file-evidence `path` is read under, and the
#: workspace directory the model sees that checkout as.
ROOT = operator_rules.ROOT
CHECKOUT_DIR = ".bureau-pipeline"
_PATH_KEYS = {"path", "quote"}

KEEP_REASON = "a genuine decision — it stays with the CEO"
#: Where a moving outcome sends the row: the lane whose own run re-does the
#: work. The move itself is the apply's.
GREEN_LIGHT_DESTINATION = "Planning"
MOVED_LINE = f"moved: Green Light → {GREEN_LIGHT_DESTINATION}"
STAYS_LINE = "stays: Green Light"
ANSWER_PREFIX = "answer: "

#: The one spelling of the tag every Operator Agent log line opens with.
LOG_TAG = "operator-agent:"
#: What a receipt opens with. Linear may drop the emoji's variation selector,
#: so a receipt is read with or without it.
RECEIPT_TAG = f"🛠️ {LOG_TAG}"
_RECEIPT_OPENS = (RECEIPT_TAG, f"🛠 {LOG_TAG}")
#: The relay lane guard's receipt (agent-bureau, DRE-7327).
GUARD_RETURN_TAG = "🚧 lane-guard-returned"

UNREAD_LIMIT = "not read by the model on this pass"
UNREAD_NO_ANSWER = "the model wrote no answer for it"

HELD_NOT_READ = "the as-built document was not read"
HELD_NO_QUOTE = "no quote from the as-built document"
HELD_GO_LIVE = "the card is the studio go-live itself"

#: How a log line names a card the agent did not decide afresh. Each one's
#: decision is rebuilt from its standing receipt, never re-made.
ACTIONS = ("decide", "spent", "apply-only", "guard-returned", "continue", "close-only")

#: The act of a `ceo` decision the screen passed as a business choice.
BUSINESS_CHOICE = "business-choice"
BUSINESS_CHOICE_LABEL = "a business choice"
#: Who wrote the choices, as `planning_escalation.screen` names them.
WRITER = "the operator agent"

#: The closes only a person or another run can make, matched in the card's
#: title and description: the pattern, and the words a `do` step names it by.
CLOSES = (
    (re.compile(r"\bRecord the outcome\b", re.IGNORECASE),
     "the console's Record the outcome"),
    (re.compile(r"\bpull request(?:'s)?\s+merge[sd]?\b"
                r"|\bmerg(?:e|es|ed|ing)\s+(?:of\s+)?(?:the|its|a|this card's)\s+pull request\b",
                re.IGNORECASE),
     "a pull request's merge"),
)

_PT = ZoneInfo("America/Los_Angeles")
_LINE_ONE = re.compile(r"^🛠️? operator-agent: (?P<outcome>\S+) — (?P<reason>.*) "
                       r"· (?P<time>\d{2}:\d{2}) PT$")
_FINGERPRINT = re.compile(r"^fingerprint: (?P<fp>[0-9a-f]{12})$")
_STEP = re.compile(r"^step: (?P<step>.+)$")
HOST_CANNOT_PREFIX = "as built, the studio host cannot: "
WAITS_ON_PREFIX = "waits on: "
ACT_PREFIX = "act: "
CHOICES_PREFIX = "choices: "
EVIDENCE_PREFIX = "evidence: "
CLOSE_LINE = "close: done"


@dataclasses.dataclass(frozen=True)
class Decision:
    """One card's outcome on one pass. `act` is a kept act, `destructive-go`
    or `business-choice` for `ceo`, and empty otherwise. `choices` is the
    screened block for a business choice and `None` for every other decision.
    `held` is why a `studio` decision is not applied on this pass, or empty."""

    card: str
    repo: str
    lane: str
    outcome: str
    reason: str
    step: str
    act: str
    deploy: bool
    evidence: list
    host_cannot: list
    finding: str
    question: str
    recommendation: str
    choices: dict | None
    held: str
    fingerprint: str

    @property
    def answer(self) -> str:
        """An `answer` decision's answer, which it carries as its Finding —
        what the facts say — or empty for every other decision."""
        return self.finding if self.outcome == ANSWER else ""

    def to_json(self) -> dict:
        """The decision as a JSON object, every field by name."""
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data) -> "Decision":
        """`to_json`'s object, or its text, read back."""
        if isinstance(data, str):
            data = json.loads(data)
        return cls(**{f.name: data[f.name] for f in dataclasses.fields(cls)})


# --------------------------------------------------------------------------- #
# the answer                                                                   #
# --------------------------------------------------------------------------- #


def _flat(text) -> str:
    """`text` on one line, every run of whitespace one space."""
    return " ".join(str(text or "").split())


def _evidence_item(item, group: str) -> bool:
    """A quote, or — on a `green-light` row — a `{"path", "quote"}` object."""
    if isinstance(item, str):
        return True
    return (group == GREEN_LIGHT_GROUP and isinstance(item, dict) and set(item) == _PATH_KEYS
            and all(isinstance(item[key], str) for key in _PATH_KEYS))


def _checked(answer, card: str | None = None, group: str = WAITING_GROUP) -> dict | None:
    """`answer` when it is an answer, else None: a JSON object, an outcome
    among `OUTCOMES` (and, on a `green-light` row, `GREEN_LIGHT_OUTCOMES`), a
    non-empty step, text where text belongs, lists of text where lists belong
    — evidence on a `green-light` row may also quote a file — and, when
    `card` is given, no other card's name."""
    if group not in GROUPS:
        raise ValueError(f"{group!r} is not one of {', '.join(GROUPS)}")
    if not isinstance(answer, dict):
        return None
    allowed = OUTCOMES + (GREEN_LIGHT_OUTCOMES if group == GREEN_LIGHT_GROUP else ())
    if answer.get("outcome") not in allowed:
        return None
    if not isinstance(answer.get("step"), str) or not answer["step"].strip():
        return None
    text = _ANSWER_TEXT + (("answer",) if group == GREEN_LIGHT_GROUP else ())
    for key in text:
        if answer.get(key) is not None and not isinstance(answer[key], str):
            return None
    for key in _ANSWER_LISTS:
        found = answer.get(key)
        if found is not None and (not isinstance(found, list) or not all(
                _evidence_item(q, group) if key == "evidence" else isinstance(q, str)
                for q in found)):
            return None
    if answer.get("deploy") is not None and not isinstance(answer["deploy"], bool):
        return None
    named = (answer.get("card") or "").strip()
    if card and named and named != card:
        return None
    return answer


def read_answer(path, group: str = WAITING_GROUP) -> dict | None:
    """The answer file at `path`, for a row of `group`, or None — see
    `_checked`. Never raises on the file."""
    try:
        with open(path, encoding="utf-8") as fh:
            answer = json.load(fh)
    except (OSError, ValueError):
        return None
    return _checked(answer, group=group)


# --------------------------------------------------------------------------- #
# the decision                                                                 #
# --------------------------------------------------------------------------- #


def fingerprint(card: dict) -> str:
    """What the card says — its title and description — and nothing else."""
    said = json.dumps([card.get("title") or "", card.get("description") or ""],
                      ensure_ascii=False)
    return hashlib.sha256(said.encode("utf-8")).hexdigest()[:12]


def _repo(card: dict) -> str:
    if card.get("repo"):
        return card["repo"]
    return next((label.split(":", 1)[1] for label in card.get("labels") or []
                 if label.startswith("repo:")), "")


def _occurs(quote: str, text: str | None) -> bool:
    """True when `quote` is in `text`, whitespace collapsed. An empty quote
    proves nothing."""
    quote = _flat(quote)
    return bool(quote) and quote in _flat(text)


def _close(card: dict) -> str | None:
    """The close only a person or another run can make that the card names."""
    said = (f"{card.get('title') or ''}\n"
            f"{operator_rules._outside_fences(card.get('description') or '')}"
            ).translate(_APOSTROPHES)
    return next((name for pattern, name in CLOSES if pattern.search(said)), None)


def _act_label(act: str, rules: dict) -> str:
    if act == BUSINESS_CHOICE:
        return BUSINESS_CHOICE_LABEL
    return next((a["label"] for a in rules["ceo_acts"] if a["id"] == act), act)


def _rule_lines(step: str, act: str, rules: dict) -> tuple:
    """The Finding, Question and Recommendation written from the rule."""
    if act == operator_rules.DESTRUCTIVE_GO:
        finding = (f"The step this card names, “{step}”, cannot be undone and "
                   "needs the CEO's go right before it.")
    elif act == BUSINESS_CHOICE:
        finding = f"The step this card names, “{step}”, is {BUSINESS_CHOICE_LABEL}."
    else:
        finding = (f"The step this card names, “{step}”, is "
                   f"{_act_label(act, rules)}, an act the CEO keeps.")
    return finding, "Shall we go ahead with this step?", console_escalation.NONE_GIVEN


def _answer_lines(answer: dict) -> tuple | None:
    """The answer's own three lines, when every one is fit for the CEO."""
    lines = tuple(_flat(answer.get(key)) for key in ("finding", "question",
                                                     "recommendation"))
    if any(planning_escalation.refusal(line) is not None for line in lines):
        return None
    if lines[1].count("?") != 1:
        return None
    return lines


def three_lines(decision: Decision) -> str:
    """The Finding, Question and Recommendation as `console_escalation.render`
    writes them — the text the screen reads, and the reason the apply hands
    `planning_escalation.escalate`."""
    recommendation = (None if decision.recommendation == console_escalation.NONE_GIVEN
                      else decision.recommendation)
    return console_escalation.render(console_escalation.Escalation(
        finding=decision.finding, question=decision.question,
        recommendation=recommendation, why=decision.reason))


def _ceo_test(base: Decision, answer: dict, match, rules: dict,
              choices_file: str | None) -> tuple:
    """Rules 2 and 3, in order: the `ceo` decision, or None and why a model's
    `ceo` is not a business choice (None when the answer did not say `ceo`).
    A `green-light` row's `keep` is this same test, run live."""
    step = base.step
    lines = _answer_lines(answer)
    replace = dataclasses.replace

    # 2. ceo by rule.
    if match is not None and match.outcome == operator_rules.CEO:
        label = _act_label(match.act, rules)
        if match.act == operator_rules.DESTRUCTIVE_GO:
            why = (f"the card names a step that cannot be undone and needs the CEO's "
                   f"go right before it (“{match.phrase}”)")
        else:
            why = f"the card names {label} (“{match.phrase}”), an act the CEO keeps"
        finding, question, recommendation = lines or _rule_lines(step, match.act, rules)
        return replace(base, outcome=CEO, act=match.act, reason=why, finding=finding,
                       question=question, recommendation=recommendation), None

    # 3. ceo as a business choice, screened.
    refused = None
    if answer["outcome"] == CEO:
        refused = f"{WRITER} wrote no choices for it"
        if choices_file and os.path.exists(choices_file):
            finding, question, recommendation = (
                lines or _rule_lines(step, BUSINESS_CHOICE, rules))
            choice = replace(base, outcome=CEO, act=BUSINESS_CHOICE, finding=finding,
                             question=question, recommendation=recommendation)
            refused = planning_escalation.screen(three_lines(choice), choices_file,
                                                 writer=WRITER)
            if refused is None:
                block = planning_escalation.read_choices(choices_file)
                if block is not None:
                    return replace(choice, choices=block), None
                refused = "its choices could not be read"
    return None, refused


def _checkout_path(path) -> str | None:
    """A file-evidence `path` as the path relative to `ROOT` it names, or
    None. One `CHECKOUT_DIR + "/"` prefix — the model's view of the checkout
    — is removed; the rest may not be empty, absolute or hold a `..` part,
    and must resolve, symlinks followed, to a regular file inside `ROOT`."""
    prefix = CHECKOUT_DIR + "/"
    rest = path[len(prefix):] if path.startswith(prefix) else path
    relative = PurePosixPath(rest)
    if not rest or relative.is_absolute() or ".." in relative.parts:
        return None
    try:
        root = Path(ROOT).resolve()
        found = (root / relative).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not found.is_relative_to(root) or not found.is_file():
        return None
    return relative.as_posix()


def _verified(items, card_text: str | None) -> list | None:
    """Every evidence item, verified, in order — a quote in `card_text`, or a
    `{"path", "quote"}` whose quote is in that file of the checkout — or None
    when there is none, one fails, or more than `MAX_PATH_EVIDENCE` quote a
    file. Whitespace is collapsed, as `done-already` reads a quote."""
    items = list(items or [])
    if not items or sum(isinstance(item, dict) for item in items) > MAX_PATH_EVIDENCE:
        return None
    kept = []
    for item in items:
        if isinstance(item, str):
            if not _occurs(item, card_text):
                return None
            kept.append(_flat(item))
            continue
        path = _checkout_path(item["path"])
        if path is None:
            return None
        try:
            text = (Path(ROOT) / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if not _occurs(item["quote"], text):
            return None
        kept.append({"path": path, "quote": _flat(item["quote"])})
    return kept


def _green_light(base: Decision, answer: dict, card_text: str | None, match, rules: dict,
                 choices_file: str | None) -> Decision | None:
    """A `green-light` row's outcome — see the module docstring."""
    kept, _refused = _ceo_test(base, answer, match, rules, choices_file)
    if kept is not None:
        return dataclasses.replace(base, outcome=KEEP, reason=KEEP_REASON, act=kept.act)
    said = answer["outcome"]
    if said not in MOVING_OUTCOMES:
        return None
    evidence = _verified(answer.get("evidence"), card_text)
    if evidence is None:
        return None
    if said == REPAIR:
        return dataclasses.replace(base, outcome=REPAIR, evidence=evidence)
    text = _flat(answer.get("answer"))
    if not text:
        return None
    return dataclasses.replace(base, outcome=ANSWER, evidence=evidence, finding=text)


def decide(card: dict, answer, card_text: str | None, as_built: str | None = None,
           rules: dict | None = None, choices_file: str | None = None,
           group: str = WAITING_GROUP) -> Decision | None:
    """The card's outcome on this pass, or None when `answer` is no answer —
    see the module docstring for the five rules, and for a `green-light`
    row's three outcomes."""
    answer = _checked(answer, card.get("card"), group)
    if answer is None:
        return None
    rules = rules if rules is not None else operator_rules.load_rules()
    match = operator_rules.rule_outcome(card.get("title"), card.get("description"), rules)
    said = answer["outcome"]
    step = _flat(answer["step"])
    reason = _flat(answer.get("reason")) or "the model read the card"
    base = Decision(
        card=card.get("card") or "", repo=_repo(card), lane=card.get("lane") or "",
        outcome=DO, reason=reason, step=step, act="", deploy=False, evidence=[],
        host_cannot=[], finding="", question="", recommendation="", choices=None,
        held="", fingerprint=fingerprint(card))
    replace = dataclasses.replace
    if group == GREEN_LIGHT_GROUP:
        return _green_light(base, answer, card_text, match, rules, choices_file)

    # 1. done-already, every quote in the card's text.
    evidence = [_flat(q) for q in answer.get("evidence") or []]
    quoted = bool(evidence) and all(_occurs(q, card_text) for q in evidence)
    if said == DONE_ALREADY and quoted:
        close = _close(card)
        if close is not None:
            return replace(base, step=f"close the card through {close}", reason=(
                f"the card's criteria are met, quoted from it, and its close is {close}, "
                "which only a person or another run makes"))
        return replace(base, outcome=DONE_ALREADY, evidence=evidence)

    # 2 and 3. ceo, by rule or as a screened business choice.
    chosen, refused = _ceo_test(base, answer, match, rules, choices_file)
    if chosen is not None:
        return chosen

    # 4. studio.
    access = _flat(answer.get("access"))
    by_rule = match is not None and match.outcome == operator_rules.STUDIO
    if by_rule or (said == STUDIO and access):
        deploy = (match.deploy if match is not None else False) or answer.get("deploy") is True
        why = (f"the step needs {match.phrase}, production access no test environment gives"
               if by_rule else reason)
        read = isinstance(as_built, str) and bool(as_built.strip())
        kept = [_flat(q) for q in answer.get("host_cannot") or []
                if read and _occurs(q, as_built)]
        if base.card == rules["studio"]["blocker"]:
            held = HELD_GO_LIVE
        elif not read:
            held = HELD_NOT_READ
        elif not kept:
            held = HELD_NO_QUOTE
        else:
            held = ""
        return replace(base, outcome=STUDIO, reason=why, deploy=deploy,
                       host_cannot=kept, held=held)

    # 5. do.
    if said == DONE_ALREADY:
        reason = "the answer's done-already quoted nothing found in the card"
    elif said == CEO:
        reason = f"the answer's ceo is not a business choice: {_flat(refused)}"
    elif said == STUDIO:
        reason = "the answer's studio named no production access"
    return replace(base, reason=reason)


# --------------------------------------------------------------------------- #
# the receipt                                                                  #
# --------------------------------------------------------------------------- #


def _clock(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(_PT).strftime("%H:%M")


def receipt_text(decision: Decision, now: datetime, rules: dict | None = None) -> str:
    """The receipt the claim posts for `decision`. A held decision has none:
    nothing is written for it on this pass."""
    if decision.held:
        raise ValueError(f"{decision.card} is held ({decision.held}) — a held "
                         "decision writes no receipt")
    lines = [f"{RECEIPT_TAG} {decision.outcome} — {_flat(decision.reason)} · "
             f"{_clock(now)} PT",
             f"fingerprint: {decision.fingerprint}",
             f"step: {_flat(decision.step)}"]
    if decision.outcome == STUDIO:
        studio = (rules if rules is not None else operator_rules.load_rules())["studio"]
        lines += [f'{HOST_CANNOT_PREFIX}"{_flat(q)}"' for q in decision.host_cannot]
        waits = [studio["blocker"]] + ([studio["deploy_epic"]] if decision.deploy else [])
        lines.append(WAITS_ON_PREFIX + ", ".join(waits))
    elif decision.outcome == CEO:
        lines.append(ACT_PREFIX + decision.act)
        if decision.act == BUSINESS_CHOICE:
            # ASCII, so U+2028, U+2029 and U+0085 — which `splitlines` breaks
            # on — are escaped and the line reads back whole.
            lines.append(CHOICES_PREFIX + json.dumps(decision.choices, ensure_ascii=True))
    elif decision.outcome == DONE_ALREADY:
        lines += [f'{EVIDENCE_PREFIX}"{_flat(q)}"' for q in decision.evidence]
        lines.append(CLOSE_LINE)
    elif decision.outcome in MOVING_OUTCOMES:
        if decision.outcome == ANSWER:
            lines.append(ANSWER_PREFIX + _flat(decision.answer))
        lines += [_evidence_line(item) for item in decision.evidence]
        lines.append(MOVED_LINE)
    elif decision.outcome == KEEP:
        lines += [ACT_PREFIX + decision.act, STAYS_LINE]
    return "\n".join(lines)


def _evidence_line(item) -> str:
    """One evidence line: `evidence: "<quote>"`, or `evidence: <path>:
    "<quote>"` for a quote from a file of the checkout."""
    if isinstance(item, dict):
        return f'{EVIDENCE_PREFIX}{item["path"]}: "{_flat(item["quote"])}"'
    return f'{EVIDENCE_PREFIX}"{_flat(item)}"'


def _evidence_back(line: str):
    """`_evidence_line`'s item read back, or None."""
    rest = line[len(EVIDENCE_PREFIX):]
    if rest.startswith('"'):
        return _quoted(line, EVIDENCE_PREFIX)
    path, sep, quote = rest.partition(': "')
    if not (path and sep and len(quote) >= 1 and quote.endswith('"')):
        return None
    return {"path": path, "quote": quote[:-1]}


#: Every first-line word `decision_from_receipt` reads, either group's.
_WORDS = OUTCOMES + GREEN_LIGHT_OUTCOMES


def _quoted(line: str, prefix: str) -> str | None:
    rest = line[len(prefix):]
    if len(rest) >= 2 and rest[0] == rest[-1] == '"':
        return rest[1:-1]
    return None


def decision_from_receipt(row: dict, body: str | None,
                          rules: dict | None = None) -> Decision | None:
    """The decision a receipt records, for the card `row`, or None when
    `body` is not a whole receipt. A `ceo` decision's three lines are written
    from the rule again: the receipt carries the act, not the words."""
    lines = (body or "").strip().splitlines()
    if len(lines) < 3:
        return None
    one, two, three = (_LINE_ONE.match(lines[0]), _FINGERPRINT.match(lines[1]),
                       _STEP.match(lines[2]))
    if not (one and two and three) or one.group("outcome") not in _WORDS:
        return None
    rules = rules if rules is not None else operator_rules.load_rules()
    outcome, step = one.group("outcome"), three.group("step")
    act, deploy, choices, closed = "", False, None, False
    evidence, host_cannot, waits = [], [], None
    answer, moved, stays = None, False, False
    for line in lines[3:]:
        if line.startswith(HOST_CANNOT_PREFIX):
            quote = _quoted(line, HOST_CANNOT_PREFIX)
            if quote is None:
                return None
            host_cannot.append(quote)
        elif line.startswith(WAITS_ON_PREFIX):
            waits = [w.strip() for w in line[len(WAITS_ON_PREFIX):].split(",")]
        elif line.startswith(ACT_PREFIX):
            act = line[len(ACT_PREFIX):].strip()
        elif line.startswith(CHOICES_PREFIX):
            try:
                choices = json.loads(line[len(CHOICES_PREFIX):])
            except ValueError:
                return None
        elif line.startswith(EVIDENCE_PREFIX):
            quote = _evidence_back(line)
            if quote is None or (isinstance(quote, dict) and outcome not in MOVING_OUTCOMES):
                return None
            evidence.append(quote)
        elif line == CLOSE_LINE:
            closed = True
        elif line.startswith(ANSWER_PREFIX) and answer is None:
            answer = line[len(ANSWER_PREFIX):].strip()
        elif line == MOVED_LINE:
            moved = True
        elif line == STAYS_LINE:
            stays = True
        else:
            return None

    if (answer is not None) != (outcome == ANSWER) or moved != (outcome in MOVING_OUTCOMES) \
            or stays != (outcome == KEEP):
        return None
    if outcome in GREEN_LIGHT_OUTCOMES and (host_cannot or waits is not None
                                            or choices is not None or closed):
        return None
    finding = question = recommendation = ""
    if outcome in MOVING_OUTCOMES:
        if not evidence or act or (outcome == ANSWER and not answer):
            return None
        finding = answer or ""
    elif outcome == KEEP:
        if not act or evidence:
            return None
    elif outcome == STUDIO:
        if not waits or rules["studio"]["blocker"] not in waits:
            return None
        deploy = rules["studio"]["deploy_epic"] in waits
    elif outcome == CEO:
        if not act or (act == BUSINESS_CHOICE) != isinstance(choices, dict):
            return None
        finding, question, recommendation = _rule_lines(step, act, rules)
    elif outcome == DONE_ALREADY and not (closed and evidence):
        return None
    return Decision(
        card=row.get("card") or row.get("identifier") or "", repo=_repo(row),
        lane=row.get("lane") or "", outcome=outcome, reason=one.group("reason"),
        step=step, act=act, deploy=deploy, evidence=evidence, host_cannot=host_cannot,
        finding=finding, question=question, recommendation=recommendation,
        choices=choices if outcome == CEO else None, held="",
        fingerprint=two.group("fp"))


def _opens(record, tags) -> bool:
    return isinstance(record, dict) and (record.get("body") or "").lstrip().startswith(tags)


def newest_receipt(records) -> dict | None:
    """The newest comment on the thread — `linear_ops.comment_records`
    entries, oldest to newest — that opens with the receipt tag and that the
    pipeline wrote, by `plan_critic.trusted_bodies`' rule. A receipt-shaped
    comment by any other author is never returned."""
    for record in reversed(list(records or [])):
        if _opens(record, _RECEIPT_OPENS) and record.get("authored_by_pipeline"):
            return record
    return None


def keep_stands(records, fingerprint: str) -> bool:
    """True exactly when the newest pipeline-authored receipt on the thread
    (`newest_receipt`) reads back as `keep` over `fingerprint`, and no record
    of any author comes after it — `records` is `linear_ops.comment_records`
    order, oldest to newest. The act registry's trailer is not a line of the
    receipt. The reading leaves a row out while it is True, and the apply's
    `claim` posts nothing while it is True; a comment after the `keep` makes
    it False, so the row is read once more for each comment it gets."""
    records = list(records or [])
    receipt = newest_receipt(records)
    if receipt is None or records[-1] is not receipt:
        return False
    decision = decision_from_receipt({}, _without_trailer(receipt.get("body")))
    return decision is not None and decision.outcome == KEEP \
        and decision.fingerprint == fingerprint


def _when(record: dict) -> datetime | None:
    stamp = record.get("created_at")
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def guard_returned(records, receipt: dict | None) -> bool:
    """True when a comment opening `GUARD_RETURN_TAG`, by any author, was
    posted after the `done-already` receipt `receipt`, or after a `do`
    receipt whose ending is a close the agent makes (`CLOSING_RULES`). Its
    only effect is that the agent writes nothing more, so its author is not
    asked. Times are Linear's own; a record with no time is placed by its
    position."""
    if not receipt:
        return False
    first = (receipt.get("body") or "").strip().splitlines()[:1]
    found = _LINE_ONE.match(first[0]) if first else None
    if not found:
        return False
    if found.group("outcome") == DO:
        ending = do_ending_from_receipt(receipt.get("body"))
        if (ending is None or ending["ending"] != CLOSED
                or ending["rule"] not in CLOSING_RULES):
            return False
    elif found.group("outcome") != DONE_ALREADY:
        return False
    records = list(records or [])
    at = _when(receipt)
    place = next((n for n, r in enumerate(records) if r is receipt), None)
    for n, record in enumerate(records):
        if record is receipt or not _opens(record, GUARD_RETURN_TAG):
            continue
        when = _when(record)
        if at is not None and when is not None:
            if when > at:
                return True
        elif place is not None and n > place:
            return True
    return False


# --------------------------------------------------------------------------- #
# a do card's claim and receipt (DRE-8923)                                     #
# --------------------------------------------------------------------------- #

DO_CLAIM, DO_CLOSE = "do-claim", "do-close"
#: The first-line words of the three comments a `do` card can get.
DO_WORDS = (DO_CLAIM, DO, DO_CLOSE)
#: A `do` step's close rules, the strings DRE-8922's `operator_do_ops.py`
#: declares, written again here so this module imports nothing from it.
CLOSE_RULES = ("record-outcome", "card-done", "own-pr-merge", "none")
NO_CLOSE_RULE = "none"
#: The close rules whose close the agent makes, and the lane guard may return.
CLOSING_RULES = tuple(rule for rule in CLOSE_RULES if rule != NO_CLOSE_RULE)
#: What `do_ending_from_receipt` names a comment's ending.
CLAIMED, CLOSED, FAILED, CONTINUES = "claimed", "close", "failed", "continues"

OUTPUT_CHARS = 1500
#: The variables whose values never reach a comment, and what they read as.
SECRET_ENV = ("GH_TOKEN", "LINEAR_API_KEY")
TOKEN_MARK = "[token]"
#: The console's and the release train's records: no line of a `do` comment
#: opens with either, so an output line that would is written after
#: `RECORD_GUARD`.
RECORD_OPENS = ("outcome:", "released as")
RECORD_GUARD = "(output) "

CLAIM_LAST = ("if no do receipt follows this, the pass died while running: the commands "
              "above may have run in part, and nothing runs them again")
NONE_NAMED = ("close: none named — the step is done and the card names no close rule; "
              "left for its owner")
NO_TRANSCRIPT = ("failed: the run wrote no transcript — the commands on the claim may "
                 "have run in part; the card is left where it was")
LEFT_NOTHING = "left changed: nothing"
_LEFT_WHERE = "the card is left where it was"
_AFTER_FORMAT = "%Y-%m-%dT%H:%MZ"

_WILL_RUN = re.compile(r"^will run: `(?P<command>.*)`$")
_PULL_REQUEST = re.compile(r"^pull request: (?P<url>\S+)$")
_COMMAND = re.compile(r"^command: `(?P<command>.*)`$")
_EXIT = re.compile(r"^exit: (?P<exit>-?\d+)$")
_FENCE = re.compile(r"^`{3,}$")
_CUT = re.compile(r"^… cut, (?P<n>\d+) characters$")
_LEFT_CHANGED = re.compile(r"^left changed: `(?P<command>.*)`$")
_CLOSE_ENDING = re.compile(r"^close: (?P<rule>%s)$" % "|".join(map(re.escape, CLOSING_RULES)))
_FAILED_ENDING = re.compile(r"^failed: `(?P<command>.*)` — exit (?P<exit>-?\d+); "
                            + re.escape(_LEFT_WHERE) + "$")
_CONTINUES_ENDING = re.compile(r"^continues: after (?P<after>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z)"
                               r" — (?P<remains>\S.*)$")
_CLOSE_FAILED = re.compile(r"^failed: (?P<what>\S.*) — " + re.escape(_LEFT_WHERE) + "$")
_ACT_TRAILER = f"{pipeline_act.TRAILER_MARK} {pipeline_act.TRAILER_TAG}:"


def _scrub(text: str) -> str:
    """`text` with every value of `SECRET_ENV` read as `TOKEN_MARK`."""
    values = {(os.environ.get(name) or "").strip() for name in SECRET_ENV} - {""}
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, TOKEN_MARK)
    return text


def _one_line(text) -> str:
    """`text` on one line: each line break a space, nothing else touched."""
    return " ".join(str(text or "").splitlines()).strip()


def _opens_record(line: str) -> bool:
    return line.lstrip().lower().startswith(RECORD_OPENS)


def _head(word: str, decision: Decision, now: datetime) -> list:
    return [f"{RECEIPT_TAG} {word} — {_flat(decision.reason)} · {_clock(now)} PT",
            f"fingerprint: {decision.fingerprint}"]


def do_claim_text(decision: Decision, command_lines, now: datetime) -> str:
    """The claim a pass posts on a `do` card before its first command runs:
    one `will run:` line per command line, in order, and `CLAIM_LAST`."""
    commands = [_one_line(line) for line in command_lines or []]
    if not commands or not all(commands):
        raise ValueError(f"{decision.card}: a claim names at least one command, none empty")
    lines = _head(DO_CLAIM, decision, now) + [f"step: {_flat(decision.step)}"]
    lines += [f"will run: `{command}`" for command in commands]
    lines.append(CLAIM_LAST)
    return _scrub("\n".join(lines))


def _output_group(output) -> list:
    """An output as the receipt quotes it: secrets read as `TOKEN_MARK`, cut
    at `OUTPUT_CHARS`, in a fence longer than any run of backticks in it."""
    text = "\n".join(_scrub(str(output or "")).splitlines())
    cut = max(0, len(text) - OUTPUT_CHARS)
    text = text[:OUTPUT_CHARS]
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    lines = ["output:", fence]
    lines += [RECORD_GUARD + line if _opens_record(line) else line
              for line in text.splitlines()]
    lines.append(fence)
    if cut:
        lines.append(f"… cut, {cut} characters")
    return lines


def _failing(commands: list) -> int | None:
    return next((n for n, ran in enumerate(commands) if ran.get("exit") != 0), None)


def _stamp(after: datetime) -> str:
    """`after` to the minute, in UTC, never earlier than it."""
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    after = after.astimezone(UTC)
    if after.second or after.microsecond:
        after = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return after.strftime(_AFTER_FORMAT)


def do_receipt_text(decision: Decision, transcript: dict | None, ending: dict,
                    now: datetime) -> str:
    """The receipt a pass posts on a `do` card after its commands ran.

    `transcript` is `operator_do.py run`'s (DRE-8924), or None when the run
    wrote none. `ending` is shaped as `do_ending_from_receipt` answers:
    `{"ending": "close", "rule": <a CLOSE_RULES value>}`, `{"ending":
    "failed"}` or `{"ending": "continues", "after": <datetime>, "remains":
    <text>}`. A dry run writes no receipt, and a run that failed, or wrote no
    transcript, takes the `failed` ending and no other."""
    commands = []
    if transcript is not None:
        if transcript.get("dry_run"):
            raise ValueError(f"{decision.card}: a dry run writes no receipt")
        if (transcript.get("card") or decision.card) != decision.card:
            raise ValueError(f"{decision.card}: the transcript is {transcript.get('card')}'s")
        commands = list(transcript.get("commands") or [])
        for ran in commands:
            if (not isinstance(ran, dict) or not _one_line(ran.get("command"))
                    or isinstance(ran.get("exit"), bool) or not isinstance(ran.get("exit"), int)):
                raise ValueError(f"{decision.card}: {ran!r} is not a command the run wrote")
    failing = _failing(commands)
    failed = transcript is None or bool(transcript.get("failed")) or failing is not None
    kind = (ending or {}).get("ending")
    if (kind == FAILED) != failed:
        raise ValueError(f"{decision.card}: a {'failed' if failed else 'finished'} run "
                         f"cannot end {kind!r}")

    lines = _head(DO, decision, now) + [f"step: {_flat(decision.step)}"]
    pull_request = _one_line((transcript or {}).get("pull_request"))
    if pull_request:
        lines.append(f"pull request: {pull_request}")
    for ran in commands:
        lines += [f"command: `{_one_line(ran.get('command'))}`",
                  f"exit: {ran['exit']}"]
        lines += _output_group(ran.get("output"))

    if kind == FAILED:
        if transcript is not None:
            if failing is None:
                raise ValueError(f"{decision.card}: a failed run names no command that failed")
            changed = [ran for ran in commands[:failing]
                       if ran.get("writes") is True and ran.get("exit") == 0]
            lines += ([f"left changed: `{_one_line(ran.get('command'))}`" for ran in changed]
                      or [LEFT_NOTHING])
            lines.append(f"failed: `{_one_line(commands[failing].get('command'))}` — exit "
                         f"{commands[failing]['exit']}; {_LEFT_WHERE}")
        else:
            lines.append(NO_TRANSCRIPT)
    elif kind == CLOSED and ending.get("rule") == NO_CLOSE_RULE:
        lines.append(NONE_NAMED)
    elif kind == CLOSED and ending.get("rule") in CLOSING_RULES:
        lines.append(f"close: {ending['rule']}")
    elif (kind == CONTINUES and isinstance(ending.get("after"), datetime)
          and _flat(ending.get("remains"))):
        lines.append(f"continues: after {_stamp(ending['after'])} — "
                     f"{_flat(ending['remains'])}")
    else:
        raise ValueError(f"{decision.card}: {ending!r} is not a do receipt's ending")
    return _scrub("\n".join(lines))


def do_close_failed_text(decision: Decision, what: str, now: datetime) -> str:
    """The line a `do` card gets, once, when its close can never land."""
    what = _flat(what)
    if not what:
        raise ValueError(f"{decision.card}: a close failure names what failed")
    if _opens_record(what):
        what = RECORD_GUARD + what
    lines = _head(DO_CLOSE, decision, now) + [f"failed: {what} — {_LEFT_WHERE}"]
    return _scrub("\n".join(lines))


def _without_trailer(body) -> str:
    """`body` without the act registry's trailer, the last line
    `pipeline_act.receipt` adds to every comment the agent posts (DRE-8925)."""
    lines = (body or "").strip().splitlines()
    if lines and lines[-1].startswith(_ACT_TRAILER):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _do_comment(body) -> dict | None:
    """Everything a claim, a receipt or a close-failure line says, or None
    when `body` is not a whole one. An output is read inside its fence alone,
    so nothing a command printed reads as a line of the receipt. The act
    registry's trailer is not a line of it."""
    lines = _without_trailer(body).splitlines()
    if len(lines) < 3:
        return None
    one, two = _LINE_ONE.match(lines[0]), _FINGERPRINT.match(lines[1])
    if not (one and two) or one.group("outcome") not in DO_WORDS:
        return None
    said = {"word": one.group("outcome"), "reason": one.group("reason"),
            "fingerprint": two.group("fp"), "step": "", "commands": [], "left_changed": [],
            "ending": None, "rule": None, "after": None, "remains": None,
            "pull_request": None, "what": None}

    if said["word"] == DO_CLOSE:
        failed = _CLOSE_FAILED.match(lines[2])
        if len(lines) != 3 or not failed:
            return None
        return {**said, "ending": FAILED, "what": failed.group("what")}

    step = _STEP.match(lines[2])
    if not step:
        return None
    said["step"] = step.group("step")

    if said["word"] == DO_CLAIM:
        will = [_WILL_RUN.match(line) for line in lines[3:-1]]
        if not will or not all(will) or lines[-1] != CLAIM_LAST:
            return None
        return {**said, "ending": CLAIMED,
                "commands": [{"command": m.group("command")} for m in will]}

    at, last = 3, len(lines) - 1
    found = _PULL_REQUEST.match(lines[at])
    if found:
        said["pull_request"], at = found.group("url"), at + 1
    while at < last and _COMMAND.match(lines[at]):
        command = _COMMAND.match(lines[at]).group("command")
        code = _EXIT.match(lines[at + 1]) if at + 1 < last else None
        if not code or lines[at + 2:at + 3] != ["output:"] or at + 3 >= last \
                or not _FENCE.match(lines[at + 3]):
            return None
        fence, start = lines[at + 3], at + 4
        close = next((n for n in range(start, last) if lines[n] == fence), None)
        if close is None:
            return None
        said["commands"].append({"command": command, "exit": int(code.group("exit")),
                                 "output": "\n".join(lines[start:close])})
        at = close + 1
        if at < last and _CUT.match(lines[at]):
            at += 1
    while at < last and (lines[at] == LEFT_NOTHING or _LEFT_CHANGED.match(lines[at])):
        found = _LEFT_CHANGED.match(lines[at])
        said["left_changed"].append(found.group("command") if found else None)
        at += 1
    if at != last:
        return None

    end = lines[last]
    close, cont = _CLOSE_ENDING.match(end), _CONTINUES_ENDING.match(end)
    if close:
        said.update(ending=CLOSED, rule=close.group("rule"))
    elif end == NONE_NAMED:
        said.update(ending=CLOSED, rule=NO_CLOSE_RULE)
    elif end == NO_TRANSCRIPT or _FAILED_ENDING.match(end):
        said.update(ending=FAILED)
    elif cont:
        after = datetime.strptime(cont.group("after"), _AFTER_FORMAT).replace(tzinfo=UTC)
        said.update(ending=CONTINUES, after=after, remains=cont.group("remains"))
    else:
        return None
    if said["left_changed"] and said["ending"] != FAILED:
        return None
    return said


def do_ending_from_receipt(body) -> dict | None:
    """What a `do` card's claim, receipt or close-failure line ends with, or
    None when `body` is none of the three, whole: `{"word", "ending",
    "rule", "after", "remains", "pull_request"}`. The time is its first
    line's, read as every receipt's is."""
    said = _do_comment(body)
    if said is None:
        return None
    return {key: said[key] for key in ("word", "ending", "rule", "after", "remains",
                                       "pull_request")}


def do_commands_from_receipt(body) -> list | None:
    """The commands a claim will run (`{"command"}` each) or a receipt ran
    (`{"command", "exit", "output"}` each), in order; `[]` for a
    close-failure line; None when `body` is none of the three, whole."""
    said = _do_comment(body)
    return None if said is None else said["commands"]


def do_decision_from_receipt(row: dict, record) -> Decision | None:
    """The `do` decision a claim, a receipt or a close-failure line stands
    for, for the card `row` — `record` a comment record or its body — or None
    when it is none of the three. The step is the `step:` line's; a
    close-failure line has none, so its step names the close that failed."""
    body = record.get("body") if isinstance(record, dict) else record
    said = _do_comment(body)
    if said is None:
        return None
    step = said["step"] or f"close the card — failed: {said['what']}"
    return Decision(
        card=row.get("card") or row.get("identifier") or "", repo=_repo(row),
        lane=row.get("lane") or "", outcome=DO, reason=said["reason"], step=step, act="",
        deploy=False, evidence=[], host_cannot=[], finding="", question="",
        recommendation="", choices=None, held="", fingerprint=said["fingerprint"])


def do_fingerprint(record) -> str | None:
    """The fingerprint a `do` card's comment names on its second line, read
    off that line alone, or None — so the newest one for a fingerprint is
    found before it is read whole."""
    body = record.get("body") if isinstance(record, dict) else record
    lines = (body or "").strip().splitlines()[1:2]
    found = _FINGERPRINT.match(lines[0]) if lines else None
    return found.group("fp") if found else None


def do_word(record) -> str | None:
    """The `DO_WORDS` word a receipt-tagged comment's first line names, read
    off that line alone, or None — so a `do` comment that does not read back
    is still known for one."""
    body = record.get("body") if isinstance(record, dict) else record
    first = (body or "").strip().splitlines()[:1]
    found = _LINE_ONE.match(first[0]) if first else None
    return found.group("outcome") if found and found.group("outcome") in DO_WORDS else None


def due_text(after: datetime) -> str:
    """A `continues:` ending's time, as the receipt writes it."""
    return _stamp(after)


# --------------------------------------------------------------------------- #
# the log lines                                                                #
# --------------------------------------------------------------------------- #


def log_line(decision: Decision, action: str = "decide") -> str:
    """One decided card's line in the decide step's log."""
    if action not in ACTIONS:
        raise ValueError(f"{action!r} is not one of {', '.join(ACTIONS)}")
    line = (f"{LOG_TAG} {decision.card} → {decision.outcome} — {_flat(decision.reason)}"
            f" · step: {_flat(decision.step)}")
    if decision.held:
        line += f" · held: {decision.held}"
    if action != "decide":
        line += f" · {action}"
    return line


def unread_line(card, why: str) -> str:
    """One card the model did not answer: no arrow and no outcome."""
    if why not in (UNREAD_LIMIT, UNREAD_NO_ANSWER):
        raise ValueError(f"{why!r} is not an unread reason")
    ident = card.get("card") if isinstance(card, dict) else card
    return f"{LOG_TAG} {ident} unread — {why}"
