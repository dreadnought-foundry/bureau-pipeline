#!/usr/bin/env python3
"""The operator agent's claim and apply (DRE-8746, epic DRE-8738).

Every Linear write the operator agent makes is here. The read-only `select`
and `decide` are DRE-8745's, in their own module; they hand this one
`decisions.json`, a JSON list of `operator_decide.Decision.to_json()` objects,
each with an `action` key — `decide`, `spent`, `apply-only` or
`guard-returned`.

    operator_agent_apply.py claim <CARD> --decisions decisions.json
    operator_agent_apply.py apply <CARD> --decisions decisions.json

## claim — the receipt that spends the card's pass

For a `studio`, `ceo` or `done-already` decision that is not held and whose
action is `decide`, `claim` posts `operator_decide.receipt_text(decision,
now)` through the act registry's receipt writer, as the act `operator-agent`.
It refuses a `do` or held decision (exit 1) and any other action (exit 3: a
receipt already stands), and posts nothing. When `operator_decide.newest_receipt` over the
card's whole thread is a pipeline-authored receipt carrying the decision's
fingerprint, the pass is spent: nothing is posted, exit 3.

## apply — one write, under the receipt, off a live read

`apply` acts only when the newest pipeline receipt on the thread reads back,
through `operator_decide.decision_from_receipt`, with the decision's
fingerprint and outcome, and not when the relay's lane guard returned the card
after it (`operator_decide.guard_returned`). It refuses a `do` or held
decision, and an action other than `decide` or `apply-only`. Each of those
writes nothing and exits 1. Then it re-reads the card; a card no longer in
the lane the decision names writes nothing, the log says why, and it exits 0.

* `studio` — a `blockedBy` relation to `studio.blocker` in
  `config/operator-agent.json`, through `linear_ops._add_blocked_by`, skipped
  when it is already there. Never to `studio.never_blocker`, to
  `studio.deploy_epic` or to any epic, and never on the blocker card itself:
  each is refused, writes nothing and exits 1. The deploy epic is named on
  the receipt only.
* `ceo` — `planning_escalation.escalate(linear_ops, card, three_lines,
  from_lane=<the card's lane>)`, which posts the note and moves the card to
  Green Light. A business choice writes its `choices` block to
  `$RUNNER_TEMP/operator-agent/choices/<CARD>.json`, is screened by
  `planning_escalation.screened` before the call, and passes that path as
  `choices_file`. A kept act is not screened and passes no file. `choices=`
  is never passed.
* `done-already` — `linear_ops.cmd_state(card, "Done", expect=(<its lane>,))`.

A write that did not land — no relation on the re-read, a card not in Green
Light after an escalation that did not stand down, a `cmd_state` that
answered False, or a write that raised — is a refusal: no comment, one line
`operator-agent: <CARD> write refused — <outcome> · <what did not land>`, and
exit 4. The receipt stands, so the next pass reads the card as `apply-only`
and tries the write again under it.

The apply writes no `Outcome:` and no `Released as` line, nor any other
comment, to get past the relay's Done guard (DRE-7327): those are the
console's and the release train's records. A close the guard returns is never
made a second time — the card waits in Queued for a person.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import operator_decide  # noqa: E402
import operator_rules  # noqa: E402
import pipeline_act  # noqa: E402
import planning_escalation  # noqa: E402

EXIT_OK = 0
EXIT_UNFINISHED = 1
EXIT_SPENT = 3
EXIT_REFUSED = 4

DONE_LANE = "Done"
#: The outcomes that write. `do` is a person's step, and writes nothing.
WRITTEN = (operator_decide.STUDIO, operator_decide.CEO, operator_decide.DONE_ALREADY)
CLAIMED = ("decide",)
APPLIED = ("decide", "apply-only")

_BLOCKERS_QUERY = """query($id: String!) { issue(id: $id) {
     inverseRelations(first: 100) {
       pageInfo { hasNextPage }
       nodes { type issue { identifier } }
     }
   } }"""


def _now() -> datetime:
    return datetime.now(UTC)


def _say(card: str, what: str) -> None:
    print(f"{operator_decide.LOG_TAG} {card} {what}")


def refusal_line(card: str, outcome: str, what: str) -> str:
    """The one line a write that did not land prints."""
    return f"{operator_decide.LOG_TAG} {card} write refused — {outcome} · {what}"


def _refused(card: str, outcome: str, what: str) -> int:
    print(refusal_line(card, outcome, what))
    return EXIT_REFUSED


# --------------------------------------------------------------------------- #
# the decision and the receipt                                                 #
# --------------------------------------------------------------------------- #


def read_decision(path: str, card: str) -> tuple:
    """`(decision, action)` for `card` in the decisions file at `path`, or
    `(None, None)` when the file names no such card. Raises when the file
    cannot be read."""
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("card") == card:
            return operator_decide.Decision.from_json(entry), entry.get("action")
    return None, None


def _receipt_body(record: dict) -> str:
    """The receipt's own lines, with the act registry's trailer taken off —
    `decision_from_receipt` reads the receipt alone."""
    trailer = f"{pipeline_act.TRAILER_MARK} {pipeline_act.TRAILER_TAG}:"
    lines = (record.get("body") or "").splitlines()
    return "\n".join(line for line in lines if not line.startswith(trailer)).strip()


def _standing(decision, records):
    """The newest pipeline receipt on the thread and the decision it reads
    back as, or `(None, None)`."""
    receipt = operator_decide.newest_receipt(records)
    if receipt is None:
        return None, None
    row = {"card": decision.card, "repo": decision.repo, "lane": decision.lane}
    return receipt, operator_decide.decision_from_receipt(row, _receipt_body(receipt))


def _post(card: str, body: str) -> None:
    """Post the receipt through the act registry. Raises on any failure, so a
    receipt that did not land is never read as posted."""
    problem = linear_ops.cmd_comment(card, pipeline_act.receipt("operator-agent", body))
    if problem:
        raise RuntimeError(problem)


# --------------------------------------------------------------------------- #
# claim                                                                        #
# --------------------------------------------------------------------------- #


def claim(card: str, decisions: str) -> int:
    decision, action = read_decision(decisions, card)
    if decision is None:
        _say(card, f"not claimed — {decisions} holds no decision for it. Nothing posted")
        return EXIT_UNFINISHED
    if decision.outcome not in WRITTEN or decision.held:
        why = f"held: {decision.held}" if decision.held else f"its outcome is {decision.outcome}"
        _say(card, f"not claimed — {why}, and nothing is written for it. Nothing posted")
        return EXIT_UNFINISHED
    if action not in CLAIMED:
        _say(card, f"not claimed — its action is {action}: a receipt already stands. "
                   "Nothing posted")
        return EXIT_SPENT
    records = linear_ops.comment_records(card, whole_thread=True)
    _receipt, standing = _standing(decision, records)
    if standing is not None and standing.fingerprint == decision.fingerprint:
        _say(card, f"not claimed — a receipt with fingerprint {decision.fingerprint} "
                   "already stands; this pass is spent. Nothing posted")
        return EXIT_SPENT
    _post(card, operator_decide.receipt_text(decision, _now()))
    print(operator_decide.log_line(decision))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# apply                                                                        #
# --------------------------------------------------------------------------- #


def _lane(issue: dict | None) -> str:
    return ((issue or {}).get("state") or {}).get("name") or ""


def _same_lane(a: str, b: str) -> bool:
    aliases = lane_contract.aliases()
    return aliases.get(a, a) == aliases.get(b, b)


def blockers(card: str) -> list[str]:
    """The cards that block `card`: its `blocks` inverse relations, read live."""
    issue = ((linear_ops.gql(_BLOCKERS_QUERY, {"id": card}) or {}).get("issue")) or {}
    nodes = (issue.get("inverseRelations") or {}).get("nodes") or []
    return [((n.get("issue") or {}).get("identifier") or "") for n in nodes
            if n.get("type") == "blocks"]


def _studio(card: str, issue: dict, rules: dict) -> int:
    studio = rules["studio"]
    target = studio["blocker"]
    if card == target:
        _say(card, f"not blocked — it is {target}, the studio host's go-live itself, "
                   "and a card is never blocked by itself. Nothing written")
        return EXIT_UNFINISHED
    if target in (studio["never_blocker"], studio["deploy_epic"]):
        _say(card, f"not blocked — {target} is never a blocker: an epic in "
                   "In Progress deadlocks the gate. Nothing written")
        return EXIT_UNFINISHED
    if target in blockers(card):
        _say(card, f"→ studio — already blocked by {target}. Nothing written")
        return EXIT_OK
    found = linear_ops.get_issue(target, fresh=True)
    if not found:
        _say(card, f"not blocked — {target} could not be read. Nothing written")
        return EXIT_UNFINISHED
    if linear_ops._is_epic_issue(target, found):
        _say(card, f"not blocked — {target} is an epic, and an epic in In Progress "
                   "deadlocks the gate. Nothing written")
        return EXIT_UNFINISHED
    what = f"the blockedBy relation to {target}"
    try:
        linear_ops._add_blocked_by(issue["id"], [target])
        landed = target in blockers(card)
    except Exception as exc:  # noqa: BLE001 — a write that raised did not land
        return _refused(card, operator_decide.STUDIO, f"{what} ({exc})")
    if not landed:
        return _refused(card, operator_decide.STUDIO, what)
    _say(card, f"→ studio — blocked by {target}")
    return EXIT_OK


def _choices_path(card: str) -> str:
    folder = os.path.join(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(),
                          "operator-agent", "choices")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{card}.json")


def _choices_file(card: str, decision, standing) -> tuple:
    """`(path, refusal)` for a `ceo` decision: no file for a kept act; for a
    business choice its block — the decision's, or the receipt's for an
    `apply-only` card — written to the run's choices folder and screened
    again, with the screen's answer when it refuses."""
    if decision.act != operator_decide.BUSINESS_CHOICE:
        return None, None
    block = decision.choices if decision.choices is not None else standing.choices
    path = _choices_path(card)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(block, fh, ensure_ascii=False)
    return path, planning_escalation.screened(operator_decide.three_lines(decision), path)


def _asked(card: str, outcome) -> int:
    """The escalation's own result read back: a stand-down is the card moving
    on, and otherwise a card not standing in Green Light is a refusal."""
    destination = planning_escalation.destination()
    if outcome.stood_down:
        _say(card, f"left alone — the escalation stood down: {outcome.stood_down}. "
                   "Nothing more written")
        return EXIT_OK
    now_in = _lane(linear_ops.get_issue(card, fresh=True))
    if not _same_lane(now_in, destination):
        return _refused(card, operator_decide.CEO,
                        f"the move to {destination} — the card is in {now_in or 'no lane'}")
    _say(card, f"→ ceo — asked in {destination}")
    return EXIT_OK


def _done(card: str, lane: str) -> int:
    what = f"the move to {DONE_LANE}"
    try:
        moved = linear_ops.cmd_state(card, DONE_LANE, expect=(lane,))
    except Exception as exc:  # noqa: BLE001 — a write that raised did not land
        return _refused(card, operator_decide.DONE_ALREADY, f"{what} ({exc})")
    if not moved:
        return _refused(card, operator_decide.DONE_ALREADY, what)
    _say(card, f"→ done-already — closed to {DONE_LANE}")
    return EXIT_OK


def apply(card: str, decisions: str) -> int:
    decision, action = read_decision(decisions, card)
    if decision is None:
        _say(card, f"not applied — {decisions} holds no decision for it. Nothing written")
        return EXIT_UNFINISHED
    if decision.outcome not in WRITTEN or decision.held:
        why = f"held: {decision.held}" if decision.held else f"its outcome is {decision.outcome}"
        _say(card, f"not applied — {why}, and nothing is written for it. Nothing written")
        return EXIT_UNFINISHED
    if action not in APPLIED:
        _say(card, f"not applied — its action is {action}. Nothing written")
        return EXIT_UNFINISHED
    records = linear_ops.comment_records(card, whole_thread=True)
    receipt, standing = _standing(decision, records)
    if (standing is None or standing.fingerprint != decision.fingerprint
            or standing.outcome != decision.outcome):
        _say(card, f"not applied — no pipeline receipt stands for {decision.outcome} "
                   f"with fingerprint {decision.fingerprint}. Nothing written")
        return EXIT_UNFINISHED
    if operator_decide.guard_returned(records, receipt):
        _say(card, f"not applied — the lane guard returned it after its receipt "
                   f"({operator_decide.GUARD_RETURN_TAG}); a person closes it. "
                   "Nothing written")
        return EXIT_UNFINISHED
    issue = linear_ops.get_issue(card, fresh=True)
    lane = _lane(issue)
    if not _same_lane(lane, decision.lane):
        _say(card, f"left alone — it is in {lane or 'no lane'}, no longer in "
                   f"{decision.lane}. Nothing written")
        return EXIT_OK
    if decision.outcome == operator_decide.STUDIO:
        return _studio(card, issue, operator_rules.load_rules())
    if decision.outcome == operator_decide.CEO:
        path, broken = _choices_file(card, decision, standing)
        if broken is not None:
            return _refused(card, operator_decide.CEO,
                            f"the screen refused the question — {broken}")
        try:
            outcome = planning_escalation.escalate(
                linear_ops, card, operator_decide.three_lines(decision),
                choices_file=path, from_lane=lane)
        except Exception as exc:  # noqa: BLE001 — a write that raised did not land
            return _refused(card, operator_decide.CEO, f"the question ({exc})")
        return _asked(card, outcome)
    return _done(card, lane)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _cmd_claim(args) -> int:
    try:
        return claim(args.card, args.decisions)
    except Exception as exc:  # noqa: BLE001 — unread or unposted: no claim stands
        print(f"ERROR: {args.card}: the claim could not be made ({exc})", file=sys.stderr)
        return EXIT_UNFINISHED


def _cmd_apply(args) -> int:
    try:
        return apply(args.card, args.decisions)
    except Exception as exc:  # noqa: BLE001 — a read failed before any write
        print(f"ERROR: {args.card}: the apply could not read what it decides on "
              f"({exc}); nothing written", file=sys.stderr)
        return EXIT_UNFINISHED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("claim", help="post the receipt that spends the card's pass")
    c.add_argument("card")
    c.add_argument("--decisions", required=True)
    c.set_defaults(fn=_cmd_claim)

    a = sub.add_parser("apply", help="make the one write the receipt names")
    a.add_argument("card")
    a.add_argument("--decisions", required=True)
    a.set_defaults(fn=_cmd_apply)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
