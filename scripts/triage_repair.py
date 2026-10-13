#!/usr/bin/env python3
"""The one repair each plan-critic park in Triage gets (DRE-7178, epic DRE-7174).

A card the plan critic's bound parks in Triage (`🔒 hold:
reason=plan-critic-bound … by=plan.yml`) had no next step. The hygiene agent's
Triage rule 3 reported it and alarmed after `HYGIENE_TRIAGE_ALARM_HOURS`. On
2026-10-12 DRE-7066 and DRE-7164 sat there about three hours each, and every
finding on both was a defect in the card a rewrite answers. This module decides
the one automatic repair each such card gets, and applies the result. It runs
no model: the Triage Repair workflow (`.github/workflows/triage-repair.yml`,
DRE-7179) runs the planner between `claim` and `apply`.

## Which cards (`select`)

The Triage lane is read once, through `board_snapshot.read_board` — the read
the hygiene agent uses. A card is in scope when it wears `needs-human` and its
newest live stamp (`hold.read_stamp`) says `plan-critic-bound`, which covers
both of plan.yml's park notes: the bound and "the revision did not finish".
`epic-rereview-twice` is out of scope: it is an approved epic's re-review, not a
planning attempt to repair.

For each card in scope the WHOLE thread is read,
`linear_ops.comment_records(card, whole_thread=True)` — never the board read's
fifty-comment window, because one planning attempt after a repair adds about
fifteen comments before the first critic's pass and more after it. The
findings come off the newest park note: the newest pipeline-authored comment
opening `🛑`, and every numbered item under `What the critic found:`
(`plan_critic.unfinished_revision_note`) or under
`plan_critic.FINDINGS_SO_FAR_HEADING` (the bound) — every item, not only the
worst of each round as `plan_bound.findings` reads them. One action per card:

  * `spent` — a pipeline-authored comment opening `RECEIPT_KEY` stands
    anywhere on the thread. Once per card, never once per park. A
    receipt-shaped comment from any other author grants nothing, by
    `plan_critic.trusted_bodies`' rule, as `plan_bound._is_receipt` reads its own.
  * `skip` — the card has children (an epic's re-plan is DRE-7181's), or the
    park note carries no numbered list.
  * `escalate` — at least one finding is one `send_back_class.classify`
    places as DECISION.
  * `repair` — every finding is REVISION or uncertain. Uncertain is the
    planner's here, for the reason `plan_bound.UNCERTAIN_BY_STAGE` gives: every
    finding at a bound is the worst of a SEND_BACK round, the critic's own
    "fix the card". All eight of DRE-7066's findings and all seven of
    DRE-7164's read uncertain.

`--out` gets the `repair`, `escalate` and `replan` cards, oldest park first, at
most `--limit`. Nothing is written to Linear.

## The claim (`claim`)

`🩹 triage-repair: started`, carrying the card's description as it stood,
fenced, so a person can put it back. The claim IS the once-per-card key: a run
that dies after it has spent the card's repair, and hygiene rule 3 alarms on a
`started` receipt once it is `TRIAGE_REPAIR_RUNNING_HOURS` old (DRE-7183).
Exit 3, posting nothing, when a receipt already stands.

## The outcome (`apply`)

The planner's files are read first, and exactly one outcome is reached:

  * `asked` — `question.md` is non-empty, whichever the action. The
    description is never set, even beside a `revised-card.md`. A question
    `planning_escalation.refusal` refuses is `unfinished` before any write.
    Writes: the lift, then `planning_escalation.escalate(...,
    from_lane="Triage")` — the note, and the move from Triage to Green Light —
    then the receipt, only when `escalate` answered `parked=True`. When it
    stood down it moved nothing, and the receipt is `unfinished`, naming why.
  * `rewritten` — the action is `repair`, there is no question, and
    `revised-card.md` and `repair-answers.md` are both non-empty. Writes: the
    description, `answer_into_card.write` (so a `## Decisions from the CEO`
    block survives the rewrite, DRE-6360), the lift, the move to Planning —
    which fires a plan run, so the critics read the rewritten card — then the
    receipt listing every finding by its number with its answer line.
  * `unfinished` — anything else, a rewrite written for an `escalate` card
    among it. The card stays in Triage, still held, for the hygiene alarm.

Every finished outcome writes in the contract's order: its own writes, then
the lift — before the move, so the plan run the move fires is not refused for
the hold — then the move, then the receipt, LAST. A failed write stops the
sequence: it is said on stderr, `unfinished` names it and every write already
made, and the exit is non-zero. A failure after the lift leaves the card in
Triage unheld, and hygiene rule 3 reads a Triage card whose newest receipt is
`unfinished` whether it is held or not (DRE-7183).

The lift line is `🔓 hold lifted: reason=plan-critic-bound because=triage-repair
by=triage_repair.py`. Its `because` is the act's NAME, never its tag
(`TRIAGE_REPAIR_TAG`): no hold line may carry a tag some reader counts, and
the receipt key is an OPENING, which a lift line (`🔓`) never has.

## CLI

    triage_repair.py select --out <file> [--limit N]
    triage_repair.py claim <CARD>                  exit 3: already repaired
    triage_repair.py apply <CARD> --action repair|escalate [--dir <dir>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import answer_into_card  # noqa: E402
import board_snapshot  # noqa: E402
import hold  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import plan_critic  # noqa: E402
import planning_escalation  # noqa: E402
import send_back_class  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------------- #
# the contract (identical in every card of DRE-7174)                           #
# --------------------------------------------------------------------------- #

LANE = "Triage"
#: Where a rewritten card goes: entering Planning fires a plan run.
PLANNING_LANE = "Planning"
IN_SCOPE_REASON = "plan-critic-bound"

#: The act, and its idempotency key: a pipeline-authored comment whose first
#: line opens `RECEIPT_KEY`, anywhere on the card, is the card's one repair.
ACT = "triage-repair"
RECEIPT_MARK = "🩹"
RECEIPT_KEY = f"{RECEIPT_MARK} {ACT}:"
#: The act's registry tag, which every receipt's trailer carries. Not the act's
#: name: the contract's lift line says `because=triage-repair`, and no hold
#: line may carry a tag some reader counts (`docs/holds.md`), so a `tag in
#: body` count of this act never reads a lift line as one of its receipts.
TRIAGE_REPAIR_TAG = "triage-repair-receipt"

STARTED = "started"
REWRITTEN = "rewritten"
ASKED = "asked"
REPLANNED = "replanned"
SPLIT = "split"
UNFINISHED = "unfinished"
OUTCOMES = (STARTED, REWRITTEN, ASKED, REPLANNED, SPLIT, UNFINISHED)

REPAIR = "repair"
ESCALATE = "escalate"
REPLAN = "replan"
SPENT = "spent"
SKIP = "skip"
ACTIONS = (REPAIR, ESCALATE, REPLAN, SPENT, SKIP)
#: The actions the workflow acts on, and so the ones `--out` carries.
WORKED = (REPAIR, ESCALATE, REPLAN)
#: The actions `apply` reaches an outcome for today.
APPLIED = (REPAIR, ESCALATE)

REPAIR_MAX_TURNS = 120
DEFAULT_LIMIT = 3

#: The lift: `hold.lift(card, LIFT_BECAUSE, LIFT_BY)`.
LIFT_BECAUSE = hold.TRIAGE_REPAIR
LIFT_BY = "triage_repair.py"

#: The planner's output files.
REVISED_CARD = "revised-card.md"
REPAIR_ANSWERS = "repair-answers.md"
QUESTION_FILE = "question.md"
QUESTION_CHOICES = "question-choices.json"

EXIT_OK = 0
EXIT_UNFINISHED = 1
EXIT_SPENT = 3

PARK_MARK = "🛑"
#: The two headings a park note lists its findings under: the "revision did
#: not finish" note's (`plan_critic.unfinished_revision_note`) and the bound's.
FINDINGS_HEADINGS = ("What the critic found:", plan_critic.FINDINGS_SO_FAR_HEADING)

_ITEM = re.compile(r"^(?P<n>\d+)[.)]\s+(?P<text>\S.*?)\s*$")
_RECEIPT = re.compile(
    rf"^{re.escape(RECEIPT_KEY)} (?P<outcome>{'|'.join(OUTCOMES)})(?:$|\s)")

# --- the words ----------------------------------------------------------------

ONCE = "This happens once per card."

# The write names an `unfinished` receipt lists, in the order they are made.
W_DESCRIPTION = "set the description"
W_DECISIONS = "write the Decisions block"
W_LIFT = "lift the hold"
W_MOVE = f"move the card to {PLANNING_LANE}"
W_ESCALATE = f"escalate the question to {planning_escalation.destination()}"
W_RECEIPT = "post the receipt"

# --------------------------------------------------------------------------- #
# reading                                                                      #
# --------------------------------------------------------------------------- #


def _body(entry) -> str:
    return (entry.get("body") if isinstance(entry, dict) else entry) or ""


def _first_line(body: str) -> str:
    text = (body or "").strip()
    return text.splitlines()[0] if text else ""


def _pipeline(entry) -> bool:
    """`plan_critic.trusted_bodies`' rule: a record counts only when the
    pipeline wrote it; a plain string is a body the caller stands behind."""
    return not isinstance(entry, dict) or bool(entry.get("authored_by_pipeline"))


def is_receipt(entry) -> bool:
    """A comment the pipeline wrote whose body opens with `RECEIPT_KEY`. Its
    opening only, so a receipt quoted inside another comment grants nothing."""
    return _pipeline(entry) and _body(entry).lstrip().startswith(RECEIPT_KEY)


def receipts(records) -> list:
    return [entry for entry in records or [] if is_receipt(entry)]


def outcome_of(entry) -> str | None:
    """The receipt's outcome word, or None when its first line names none."""
    match = _RECEIPT.match(_first_line(_body(entry)))
    return match.group("outcome") if match else None


def park_note(records):
    """The newest pipeline-authored comment opening `🛑`, or None."""
    for entry in reversed(list(records or [])):
        if _pipeline(entry) and _body(entry).lstrip().startswith(PARK_MARK):
            return entry
    return None


def park_findings(body: str) -> list[str]:
    """Every numbered item at column zero under the park note's findings
    heading, in its order. [] when the note carries no such list."""
    lines = (body or "").splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.strip().startswith(FINDINGS_HEADINGS)), None)
    if start is None:
        return []
    found: list[str] = []
    for line in lines[start + 1:]:
        match = _ITEM.match(line)
        if match:
            found.append(match.group("text"))
        elif line.strip() and found:
            break
    return found


def decisions(found: list[str]) -> list[str]:
    return [f for f in found if send_back_class.classify(f) == send_back_class.DECISION]


def _labels(card: dict) -> list[str]:
    return [(n or {}).get("name") or "" for n in ((card.get("labels") or {}).get("nodes") or [])]


def _window_bodies(card: dict) -> list[str]:
    return [(n or {}).get("body") or ""
            for n in linear_ops.window_nodes(card.get("comments"))]


def in_scope(card: dict) -> bool:
    """In Triage, wearing `needs-human`, its newest live stamp `plan-critic-bound`."""
    if ((card.get("state") or {}).get("name") or "") != LANE:
        return False
    if hold.HOLD_LABEL not in [n.lower() for n in _labels(card)]:
        return False
    stamp = hold.read_stamp(_window_bodies(card))
    return bool(stamp) and stamp["reason"] == IN_SCOPE_REASON


def _repo(card: dict) -> str:
    """The card's `repo:` label as `owner/name`, through `config/repo-map.json`
    — the slug itself when the map does not carry it, "" with no label."""
    for name in _labels(card):
        if name.lower().startswith("repo:"):
            slug = name[len("repo:"):].strip().lower()
            try:
                with open(os.path.join(ROOT, "config", "repo-map.json"),
                          encoding="utf-8") as fh:
                    return json.load(fh).get(slug) or slug
            except (OSError, ValueError):
                return slug
    return ""


def decide(card: dict, records) -> dict:
    """The one action for one card in scope, off its whole thread. Pure."""
    found: list[str] = []
    note = park_note(records)
    if note is not None:
        found = park_findings(_body(note))
    picked = decisions(found)
    spent = receipts(records)
    if spent:
        word = outcome_of(spent[-1]) or "unreadable"
        action, why = SPENT, (f"the card has had its one automatic repair — a "
                              f"`{word}` receipt stands on its thread")
    elif (card.get("children") or {}).get("nodes"):
        action, why = SKIP, ("the card has children — an epic is re-planned, "
                             "not rewritten")
    elif not found:
        action, why = SKIP, ("the park note carries no numbered list of findings"
                             if note is not None else "the card carries no park note")
    elif picked:
        action, why = ESCALATE, (f"{len(picked)} of {len(found)} finding(s) name a "
                                 "choice only the CEO can make")
    else:
        action, why = REPAIR, (f"all {len(found)} finding(s) are the planner's to "
                               "answer in a rewrite")
    return {
        "card": card.get("identifier") or "",
        "title": card.get("title") or "",
        "repo": _repo(card),
        "action": action,
        "why": why,
        "findings": found,
        "decisions": picked,
        "max_turns": REPAIR_MAX_TURNS,
        "_parked_at": (note.get("created_at") or "") if isinstance(note, dict) else "",
    }


def select(limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Every card in scope, decided, and the worked ones, oldest park first,
    at most `limit`. Prints one line per card in scope."""
    cards, _requests = board_snapshot.read_board((LANE,))
    rows: list[dict] = []
    for card in cards:
        if not in_scope(card):
            continue
        ident = card.get("identifier") or ""
        try:
            records = linear_ops.comment_records(ident, whole_thread=True)
        except Exception as exc:  # noqa: BLE001 — one unread card, never the pass
            print(f"triage-repair: {ident} — could not read its thread ({exc}); "
                  "skipped this pass", file=sys.stderr)
            continue
        row = decide(card, records)
        print(f"triage-repair: {ident} → {row['action']} — {row['why']}")
        if row["action"] in WORKED:
            rows.append(row)
    rows.sort(key=lambda r: r["_parked_at"])
    return [{k: v for k, v in r.items() if not k.startswith("_")}
            for r in rows[:max(0, limit)]]


# --------------------------------------------------------------------------- #
# the receipts                                                                 #
# --------------------------------------------------------------------------- #


def _flat(text: str) -> str:
    return " ".join(str(text).split())


def _fence(text: str) -> str:
    """A backtick fence longer than any backtick run inside `text`."""
    longest = max((len(run) for run in re.findall(r"`+", text or "")), default=0)
    return "`" * max(3, longest + 1)


def started_body(description: str) -> str:
    fence = _fence(description)
    return "\n\n".join([
        f"{RECEIPT_KEY} {STARTED} — this card was parked in {LANE} by the plan "
        "critic's bound, and the planner is now answering every finding of its "
        f"park note once, without a person. {ONCE} The description as it stood "
        "before the repair, so a person can put it back:",
        f"{fence}\n{description or ''}\n{fence}",
    ])


def _answer_lines(text: str) -> dict:
    out: dict = {}
    for line in (text or "").splitlines():
        match = _ITEM.match(line.strip())
        if match:
            out.setdefault(int(match.group("n")), match.group("text"))
    return out


def rewritten_body(found: list[str], answers_text: str) -> str:
    answers = _answer_lines(answers_text)
    listed = "\n".join(
        f"{n}. {_flat(f)}\n   Answer: "
        f"{_flat(answers.get(n) or 'the planner wrote no answer line for this finding')}"
        for n, f in enumerate(found, 1))
    return "\n\n".join([
        f"{RECEIPT_KEY} {REWRITTEN} — the planner rewrote this card once to "
        f"answer every finding of its park note, the hold is lifted, and the "
        f"card is back in {PLANNING_LANE}, where a fresh plan run has the "
        f"critics read the rewritten card. {ONCE} The findings, each with its "
        "answer:",
        listed or "(the park note carried no findings)",
    ])


def asked_body(set_aside: bool) -> str:
    parts = [
        f"{RECEIPT_KEY} {ASKED} — the planner found a choice on this card only "
        "the CEO can make, so it is asked as one question in "
        f"{planning_escalation.destination()} rather than guessed, and the "
        f"card's description is unchanged. {ONCE}",
    ]
    if set_aside:
        parts.append("The planner's rewrite was set aside: with a question "
                     "open, the description is not set.")
    return "\n\n".join(parts)


def unfinished_body(what: str, made: list[str]) -> str:
    done = ", ".join(made) if made else "none"
    return (f"{RECEIPT_KEY} {UNFINISHED} — {what}. Writes already made: {done}. "
            f"The card has had its one automatic repair; a person reads it from "
            "here.")


def _post(card: str, body: str) -> None:
    """Post one receipt through the act registry. Raises on any failure,
    including a comment cap, so a caller never reads a receipt as posted that
    did not land."""
    problem = linear_ops.cmd_comment(card, pipeline_act.receipt("triage-repair", body))
    if problem:
        raise RuntimeError(problem)


# --------------------------------------------------------------------------- #
# claim                                                                        #
# --------------------------------------------------------------------------- #


def claim(card: str) -> int:
    records = linear_ops.comment_records(card, whole_thread=True)
    if receipts(records):
        print(f"triage-repair: {card} — a receipt already stands; its one "
              f"repair is spent. Exit {EXIT_SPENT}, nothing posted")
        return EXIT_SPENT
    description = answer_into_card._read_description(card)
    _post(card, started_body(description))
    print(f"triage-repair: {card} → {STARTED}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# apply                                                                        #
# --------------------------------------------------------------------------- #


def _read(directory: str | None, name: str) -> str:
    if not directory:
        return ""
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


class _Stopped(Exception):
    """A write failed; `apply` posts `unfinished` and exits non-zero."""

    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class _Writes:
    """The sequence of one outcome's writes: each runs, or the sequence stops."""

    def __init__(self, card: str):
        self.card = card
        self.made: list[str] = []

    def do(self, name: str, fn) -> object:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 — every failure is named, then stops
            raise _Stopped(f"the write to {name} failed ({exc})") from exc
        self.made.append(name)
        return result


def _decisions_block(card: str) -> None:
    did, _ = answer_into_card.write(card)
    if did.startswith("could not write") or did.startswith("could not check: could not read"):
        raise RuntimeError(did)


def _move_to_planning(card: str) -> None:
    if not linear_ops.cmd_state(card, PLANNING_LANE):
        raise RuntimeError(f"a guard refused the move to {PLANNING_LANE}")


def _finish(card: str, writes: _Writes, what: str) -> int:
    """Post `unfinished` naming `what` and the writes made, and exit non-zero."""
    print(f"ERROR: {card}: {what}. Writes already made: "
          f"{', '.join(writes.made) or 'none'}", file=sys.stderr)
    try:
        _post(card, unfinished_body(what, writes.made))
    except Exception as exc:  # noqa: BLE001 — the newest receipt stays `started`
        print(f"ERROR: {card}: the unfinished receipt could not be posted ({exc}); "
              "the newest receipt is still the claim", file=sys.stderr)
    print(f"triage-repair: {card} → {UNFINISHED}")
    return EXIT_UNFINISHED


def apply(card: str, action: str, directory: str | None) -> int:
    question = _read(directory, QUESTION_FILE)
    revised = _read(directory, REVISED_CARD)
    answers = _read(directory, REPAIR_ANSWERS)
    writes = _Writes(card)
    try:
        records = linear_ops.comment_records(card, whole_thread=True)
    except Exception as exc:  # noqa: BLE001
        return _finish(card, writes, f"the card's thread could not be read ({exc})")
    stamp = hold.read_stamp([_body(r) for r in records])
    reason = stamp["reason"] if stamp else "manual"

    try:
        if question.strip():
            refused = planning_escalation.refusal(question)
            if refused is not None:
                return _finish(card, writes, "the planner's question was refused "
                               f"before it reached the CEO — {refused}")
            choices = planning_escalation.read_choices(
                os.path.join(directory, QUESTION_CHOICES) if directory else None)
            writes.do(W_LIFT, lambda: hold.lift(card, LIFT_BECAUSE, LIFT_BY,
                                                reason=reason))
            outcome = writes.do(W_ESCALATE, lambda: planning_escalation.escalate(
                linear_ops, card, question, choices=choices, from_lane=LANE))
            if not outcome.parked:
                return _finish(card, writes, "the escalation stood down and moved "
                               f"nothing — {outcome.stood_down}")
            writes.do(W_RECEIPT, lambda: _post(
                card, asked_body(bool(revised.strip()))))
            print(f"triage-repair: {card} → {ASKED}")
            return EXIT_OK

        missing = [name for name, text in ((REVISED_CARD, revised),
                                           (REPAIR_ANSWERS, answers))
                   if not text.strip()]
        if action != REPAIR:
            return _finish(card, writes, f"the action is {action} and the planner "
                           f"wrote no {QUESTION_FILE}, so there is nothing to ask"
                           + (" — a rewrite is not this action's outcome"
                              if revised.strip() else ""))
        if missing:
            return _finish(card, writes, "the planner wrote no "
                           + " and no ".join(missing))
        found = park_findings(_body(park_note(records)))
        writes.do(W_DESCRIPTION, lambda: linear_ops.set_description(card, revised))
        writes.do(W_DECISIONS, lambda: _decisions_block(card))
        writes.do(W_LIFT, lambda: hold.lift(card, LIFT_BECAUSE, LIFT_BY,
                                            reason=reason))
        writes.do(W_MOVE, lambda: _move_to_planning(card))
        writes.do(W_RECEIPT, lambda: _post(card, rewritten_body(found, answers)))
        print(f"triage-repair: {card} → {REWRITTEN}")
        return EXIT_OK
    except _Stopped as stop:
        return _finish(card, writes, stop.what)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _cmd_select(args) -> int:
    rows = select(args.limit)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return EXIT_OK


def _cmd_claim(args) -> int:
    try:
        return claim(args.card)
    except Exception as exc:  # noqa: BLE001 — unread or unposted: no claim stands
        print(f"ERROR: {args.card}: the claim could not be made ({exc})", file=sys.stderr)
        return EXIT_UNFINISHED


def _cmd_apply(args) -> int:
    return apply(args.card, args.action, args.dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="decide each card's repair; writes nothing")
    s.add_argument("--out", required=True)
    s.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    s.set_defaults(fn=_cmd_select)

    c = sub.add_parser("claim", help="post the started receipt, once per card")
    c.add_argument("card")
    c.set_defaults(fn=_cmd_claim)

    a = sub.add_parser("apply", help="apply what the planner wrote")
    a.add_argument("card")
    a.add_argument("--action", required=True, choices=APPLIED)
    a.add_argument("--dir", default=None)
    a.set_defaults(fn=_cmd_apply)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
