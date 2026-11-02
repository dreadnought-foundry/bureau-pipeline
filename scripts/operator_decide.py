#!/usr/bin/env python3
"""The operator agent's decision (DRE-8744, epic DRE-8738).

A card waiting on a person gets at most one outcome per pass — `do`, `studio`,
`ceo` or `done-already` — and a card the model has not answered gets none: it
is `unread` on that pass, has no write and is never counted as `do`. This
module picks the outcome from the fixed routes of `operator_rules.py`
(DRE-8743) and the model's answer, and it guards that answer so a model can
never put a non-decision in front of the CEO. It also writes a decision down
as a receipt and reads it back, so no sibling restates the format.

    read_answer(path) -> dict | None
    decide(card, answer, card_text, as_built=None, rules=None, choices_file=None)
        -> Decision | None
    three_lines(decision) -> str
    fingerprint(card) -> str
    receipt_text(decision, now) -> str
    decision_from_receipt(row, body) -> Decision | None
    newest_receipt(records) -> dict | None
    guard_returned(records, receipt) -> bool
    log_line(decision, action="decide") -> str
    unread_line(card, why) -> str

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
`close: done`. No line opens `Outcome:` or `Released as`: a `done-already`
close moves the card to `Done` knowing the relay's lane guard (DRE-7327) may
return it, and never forges a record to get past that guard. A comment opening
`GUARD_RETURN_TAG` after the receipt is `guard_returned`, and the agent writes
nothing more on that card.

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
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import console_escalation  # noqa: E402
import operator_rules  # noqa: E402
import planning_escalation  # noqa: E402
from operator_step_kind import _APOSTROPHES  # noqa: E402

OUTCOMES = ("do", "studio", "ceo", "done-already")
DO, STUDIO, CEO, DONE_ALREADY = OUTCOMES

#: The keys of a model's answer file. A choices block is never one of them.
ANSWER_KEYS = ("card", "outcome", "step", "reason", "evidence", "access", "deploy",
               "host_cannot", "finding", "question", "recommendation")
_ANSWER_TEXT = ("card", "step", "reason", "access", "finding", "question",
                "recommendation")
_ANSWER_LISTS = ("evidence", "host_cannot")

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
ACTIONS = ("decide", "spent", "apply-only", "guard-returned")

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


def _checked(answer, card: str | None = None) -> dict | None:
    """`answer` when it is an answer, else None: a JSON object, an outcome
    among `OUTCOMES`, a non-empty step, text where text belongs, lists of text
    where lists belong, and — when `card` is given — no other card's name."""
    if not isinstance(answer, dict):
        return None
    if answer.get("outcome") not in OUTCOMES:
        return None
    if not isinstance(answer.get("step"), str) or not answer["step"].strip():
        return None
    for key in _ANSWER_TEXT:
        if answer.get(key) is not None and not isinstance(answer[key], str):
            return None
    for key in _ANSWER_LISTS:
        found = answer.get(key)
        if found is not None and (not isinstance(found, list)
                                  or not all(isinstance(q, str) for q in found)):
            return None
    if answer.get("deploy") is not None and not isinstance(answer["deploy"], bool):
        return None
    named = (answer.get("card") or "").strip()
    if card and named and named != card:
        return None
    return answer


def read_answer(path) -> dict | None:
    """The answer file at `path`, or None — see `_checked`. Never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            answer = json.load(fh)
    except (OSError, ValueError):
        return None
    return _checked(answer)


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


def decide(card: dict, answer, card_text: str | None, as_built: str | None = None,
           rules: dict | None = None, choices_file: str | None = None) -> Decision | None:
    """The card's outcome on this pass, or None when `answer` is no answer —
    see the module docstring for the five rules."""
    answer = _checked(answer, card.get("card"))
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

    lines = _answer_lines(answer)

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
                       question=question, recommendation=recommendation)

    # 3. ceo as a business choice, screened.
    refused = None
    if said == CEO:
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
                    return replace(choice, choices=block)
                refused = "its choices could not be read"

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
    return "\n".join(lines)


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
    if not (one and two and three) or one.group("outcome") not in OUTCOMES:
        return None
    rules = rules if rules is not None else operator_rules.load_rules()
    outcome, step = one.group("outcome"), three.group("step")
    act, deploy, choices, closed = "", False, None, False
    evidence, host_cannot, waits = [], [], None
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
            quote = _quoted(line, EVIDENCE_PREFIX)
            if quote is None:
                return None
            evidence.append(quote)
        elif line == CLOSE_LINE:
            closed = True
        else:
            return None

    finding = question = recommendation = ""
    if outcome == STUDIO:
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
    posted after the `done-already` receipt `receipt`. Its only effect is that
    the agent writes nothing more, so its author is not asked. Times are
    Linear's own; a record with no time is placed by its position."""
    if not receipt:
        return False
    first = (receipt.get("body") or "").strip().splitlines()[:1]
    found = _LINE_ONE.match(first[0]) if first else None
    if not found or found.group("outcome") != DONE_ALREADY:
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
