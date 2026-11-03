#!/usr/bin/env python3
"""Which `Green Light` rows are the Operator Agent's to take (DRE-9048, epic
DRE-8738).

    reading(card, records, viewer) -> Reading

`Green Light` is the CEO's decision queue. Some rows in it are not decisions —
a run that stopped, or a question the facts answer — and those are the
agent's. This module rules out, before any model reads a card, every row that
is never the agent's. It reads one card and its thread and answers `taken` or
not, with a reason, and it writes nothing, runs no model and makes no Linear
read of its own.

`card` is the board read's card dict (`board_snapshot.CARD_QUERY`'s shape);
`records` is `linear_ops.comment_records(ident, whole_thread=True)`, oldest to
newest, and it is the thread every rule reads — never the card's own comment
window; `viewer` is the pipeline's own Linear user id, the author
`hygiene_green_light` trusts. With no viewer no row is taken (`NO_VIEWER`), as
the hygiene lane skips itself: no receipt can be told from a stranger's.

## The seven rules, in order — the first that holds rules the row out

1. **A row the agent already read.** The newest pipeline receipt
   (`operator_decide.newest_receipt`) reads back as `ceo`, whatever its
   fingerprint — the agent sent it there (`SENT_WHY`); or
   `operator_decide.keep_stands` holds over the card's fingerprint — the agent
   read it and left it with the CEO (`KEPT_WHY`). A `keep` with a comment after
   it, or over a changed title or description, rules nothing out: the model
   reads the row once more, and the fresh receipt that read ends in rules it
   out again on the pass after. One re-read per comment, never a loop.
2. **A plan waiting for the CEO's approval** (`PLAN_WHY`): the newest planning
   receipt (`hygiene_green_light.newest_receipt`) is a plan-critic PASS, or
   the card wears `epic_cap.QUEUED_LABEL`.
3. **A growth question** (`GROWTH_WHY`): the title opens
   `epic_growth.TITLE_PREFIX`.
4. **A signed-answer park** (`SIGNED_WHY`): a `PROOF:` card
   (`proof_and_demo.is_proof`); an open `🔬 proof-waiting` hold naming
   `proof_record.CEO_PRESS` (`proof_record.open_holds`); or a comment
   `spoken_thread.voices` labels `ceo-via-console` newer than the row's newest
   park — the newest comment the pipeline wrote that opens one of `PARK_OPENS`,
   or the start of the thread when it has none.
5. **A row the hygiene agent's Green Light rule acts on, or reads as a stall**
   (`HYGIENE_WHY`), by that lane's own functions: its newest planning receipt is
   a plan-stage limit death, a transport receipt, or a planning question that
   `is_stall` or `asks_for` reads — every stall park, whether the lane re-sends
   it or leaves it as a second stall. The two agents never act on one row.
6. **A notice or a request to a person** (`NOTICE_WHY`): the weekly report (a
   title opening `WEEKLY_REPORT_OPENS`), a deferred release (a title as
   `release_deferral.title` writes it), a code-owner hold the merge gate's own
   reader still reads as `hold` (`code_owner_hold.latest_gate_marker`), or a
   design question whose part `design_answer` has not handed back.
7. **A business choice already screened** (`CHOICE_WHY`): the newest question
   the pipeline posted carries an `escalation-choices` block
   `planning_escalation.screen` passes, or `operator_rules.rule_outcome` answers
   `ceo` for the title and description — an act the CEO keeps.

Everything else is `taken` (`TAKEN_WHY`). Whether a taken row is truly not a
decision is still the model's reading and part 1's `ceo` test, run live in
`operator_decide.decide` — this module only rules out what is never the
agent's.

`planning_escalation.screen` screens a file, never a dict, so rule 7 writes
the block to a scratch file that is removed before it returns. That is the
one write, and it never leaves the runner.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import code_owner_hold  # noqa: E402 — ONE reader of the merge gate's hold marker
import design_answer  # noqa: E402 — ONE reader of a design question's state
import epic_cap  # noqa: E402 — the `epic-queued` label
import epic_growth  # noqa: E402 — the growth question's title
import hygiene_green_light  # noqa: E402 — the hygiene lane's Green Light rule, read-only
import operator_decide  # noqa: E402 — ONE reading of the agent's own receipts
import operator_rules  # noqa: E402 — the acts the CEO keeps
import planning_escalation  # noqa: E402 — the question mark, its block and its screen
import proof_and_demo  # noqa: E402 — ONE reading of a PROOF: title
import proof_record  # noqa: E402 — the proof run's holds, and the CEO's press
import release_deferral  # noqa: E402 — the deferred release's title
import spoken_thread  # noqa: E402 — ONE reader of the CEO's signed words

LANE = "Green Light"

SENT_WHY = "the agent read it and sent it to the CEO"
KEPT_WHY = "the agent read it and left it with the CEO"
PLAN_WHY = "a plan waiting for the CEO's approval"
GROWTH_WHY = "a growth question the CEO answers in a signed answer"
SIGNED_WHY = "a park only the CEO's signed answer lifts, or one the CEO has already answered"
HYGIENE_WHY = "a row the hygiene agent's Green Light rule acts on, or reads as a stall"
NOTICE_WHY = "a notice or a request to a person, not a question"
CHOICE_WHY = "already screened as a business choice, or it names an act the CEO keeps"
TAKEN_WHY = "not a decision this agent is barred from"
NO_VIEWER = ("the pipeline's Linear user could not be read — no receipt can be told "
             "from a stranger's")

#: The weekly agent report's title. Its writer lives in agent-bureau
#: (`console/backend/weekly_agent_report_card.py`, DRE-5020), outside this
#: repository; `config/lane-contract.json`'s kind (e) names the same title.
WEEKLY_REPORT_OPENS = "What broke the agents — week ending"

#: How a deferred release's title reads before its reason:
#: `<repo>: <surface> release deferred`, cut from the writer's own prefix.
DEFERRED_ENDS = release_deferral.title_prefix("", "").partition(": ")[2]
#: What separates that prefix from the reason in `release_deferral.title`.
_DEFERRED_REASON = release_deferral.title("", "", "")[len(release_deferral.title_prefix("", "")):]

#: What a park opens with: a question to the CEO, a proof hold, a design
#: question. A signed answer counts only after the newest one of these.
PARK_OPENS = (planning_escalation.ESCALATION_MARK, proof_record.HOLD_MARK,
              design_answer.QUESTION_MARK)

#: The planning receipts the hygiene lane re-sends or reads as a stall.
_HYGIENE_KINDS = ("limit-death", "transport", "escalation")


@dataclasses.dataclass(frozen=True)
class Reading:
    """One row's answer: is it the agent's to take, and why."""

    taken: bool
    why: str


# --------------------------------------------------------------------------- #
# the thread                                                                   #
# --------------------------------------------------------------------------- #


def _nodes(records, viewer: str) -> list:
    """`records` as Linear's comment nodes, oldest to newest: the pipeline's
    own comments under `viewer`, everyone else's under no user — so no reader
    here can take a stranger's comment for the pipeline's."""
    return [{"body": record.get("body") or "", "createdAt": record.get("created_at"),
             "user": {"id": viewer} if record.get("authored_by_pipeline") is True else None}
            for record in records or [] if isinstance(record, dict)]


def _thread_card(card: dict, nodes: list) -> dict:
    """The card with `nodes` as its comments, newest first as the board
    reads, for the hygiene lane's readers."""
    return {**card, "comments": {"nodes": list(reversed(nodes))}}


def _labels(card: dict) -> list:
    return [(n or {}).get("name") or "" for n in ((card.get("labels") or {}).get("nodes") or [])]


def _ident(card: dict) -> str:
    return card.get("identifier") or card.get("card") or ""


# --------------------------------------------------------------------------- #
# the rules                                                                    #
# --------------------------------------------------------------------------- #


def _read_already(card: dict, records: list) -> str | None:
    receipt = operator_decide.newest_receipt(records)
    if receipt is not None:
        decision = operator_decide.decision_from_receipt(
            {}, operator_decide._without_trailer(receipt.get("body")))
        if decision is not None and decision.outcome == operator_decide.CEO:
            return SENT_WHY
    if operator_decide.keep_stands(records, operator_decide.fingerprint(card)):
        return KEPT_WHY
    return None


def _plan(card: dict, receipt: dict | None) -> str | None:
    if receipt is not None and receipt["kind"] == "critic" and receipt["result"] == "PASS":
        return PLAN_WHY
    if epic_cap.QUEUED_LABEL in _labels(card):
        return PLAN_WHY
    return None


def _growth(card: dict) -> str | None:
    return GROWTH_WHY if (card.get("title") or "").startswith(epic_growth.TITLE_PREFIX) else None


def _answered(voices: list) -> bool:
    """A signed answer newer than the newest park the pipeline wrote."""
    park = max((n for n, v in enumerate(voices) if v.kind == spoken_thread.PIPELINE
                and (v.body or "").lstrip().startswith(PARK_OPENS)), default=-1)
    return any(v.kind == spoken_thread.CEO_VIA_CONSOLE for v in voices[park + 1:])


def _signed(card: dict, voices: list) -> str | None:
    if proof_and_demo.is_proof(card.get("title")):
        return SIGNED_WHY
    if any(proof_record.CEO_PRESS in line for line in proof_record.open_holds(voices)):
        return SIGNED_WHY
    return SIGNED_WHY if _answered(voices) else None


def _hygiene(receipt: dict | None) -> str | None:
    if receipt is None or receipt["kind"] not in _HYGIENE_KINDS:
        return None
    if receipt["kind"] == "limit-death":
        return HYGIENE_WHY if receipt["marker"]["stage"] == "plan" else None
    if receipt["kind"] == "transport":
        return HYGIENE_WHY
    reason = receipt["reason"]
    if receipt["question"] and (hygiene_green_light.is_stall(reason)
                                or hygiene_green_light.asks_for(reason)):
        return HYGIENE_WHY
    return None


def _deferred(title: str) -> bool:
    head = title.split(_DEFERRED_REASON, 1)[0] if _DEFERRED_REASON else title
    return bool(DEFERRED_ENDS) and head.endswith(DEFERRED_ENDS) and ": " in head


def _owner_held(records: list) -> bool:
    prs = set()
    for record in records:
        if record.get("authored_by_pipeline") is not True:
            continue
        found = code_owner_hold._CARD_MARKER_RE.match((record.get("body") or "").split("\n", 1)[0])
        if found:
            prs.add(found.group(2))
    return any(code_owner_hold.latest_gate_marker(records, pr) == "hold" for pr in prs)


def _design_open(voices: list) -> bool:
    return any(design_answer.state_of(voices, part, path) != design_answer.HANDED_BACK
               for part, path in design_answer.parts(voices))


def _notice(card: dict, records: list, voices: list) -> str | None:
    title = card.get("title") or ""
    if title.startswith(WEEKLY_REPORT_OPENS) or _deferred(title):
        return NOTICE_WHY
    if _owner_held(records) or _design_open(voices):
        return NOTICE_WHY
    return None


def _newest_question(records: list) -> dict | None:
    for record in reversed(records):
        if record.get("authored_by_pipeline") is True and (record.get("body") or "").lstrip() \
                .startswith(planning_escalation.ESCALATION_MARK):
            return record
    return None


def _screened(body: str) -> bool:
    """The question's `escalation-choices` block passes the screen — run over
    a scratch file, because the screen reads a file and never a dict."""
    block = planning_escalation.parse_choices(body)
    if block is None:
        return False
    reason = hygiene_green_light.escalation_reason(body) or body
    with tempfile.TemporaryDirectory() as scratch:
        path = os.path.join(scratch, "choices.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(block, fh)
        return planning_escalation.screen(reason, path, writer=operator_decide.WRITER) is None


def _choice(card: dict, records: list) -> str | None:
    question = _newest_question(records)
    if question is not None and _screened(question.get("body") or ""):
        return CHOICE_WHY
    match = operator_rules.rule_outcome(card.get("title"), card.get("description"))
    if match is not None and match.outcome == operator_rules.CEO:
        return CHOICE_WHY
    return None


# --------------------------------------------------------------------------- #
# the reading                                                                  #
# --------------------------------------------------------------------------- #


def reading(card: dict, records, viewer: str | None) -> Reading:
    """Is this `Green Light` row the agent's — see the module docstring."""
    if not viewer:
        return Reading(taken=False, why=NO_VIEWER)
    records = [r for r in records or [] if isinstance(r, dict)]
    nodes = _nodes(records, viewer)
    receipt = hygiene_green_light.newest_receipt(_thread_card(card, nodes), viewer)
    why = _read_already(card, records) or _plan(card, receipt) or _growth(card)
    if why is None:
        voices = spoken_thread.voices(nodes, viewer, card=_ident(card))
        why = (_signed(card, voices) or _hygiene(receipt)
               or _notice(card, records, voices) or _choice(card, records))
    return Reading(taken=False, why=why) if why else Reading(taken=True, why=TAKEN_WHY)
