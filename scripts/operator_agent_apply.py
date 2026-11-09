#!/usr/bin/env python3
"""The operator agent's claim and apply (DRE-8746, epic DRE-8738).

Every Linear write the operator agent makes is here. The read-only `select`
and `decide` are DRE-8745's, in their own module; they hand this one
`decisions.json`, a JSON list of `operator_decide.Decision.to_json()` objects,
each with an `action` key — `decide`, `spent`, `apply-only` or
`guard-returned`.

    operator_agent_apply.py claim <CARD> --decisions decisions.json
    operator_agent_apply.py apply <CARD> --decisions decisions.json
    operator_agent_apply.py do-claim <CARD> --decisions decisions.json --check <CARD>.check.json
    operator_agent_apply.py do-post <CARD> --decisions decisions.json --transcript transcript.json
                                   --plan <CARD>.plan.json
    operator_agent_apply.py do-close <CARD>

## claim — the receipt that spends the card's pass

For a `studio`, `ceo` or `done-already` decision, or a Green Light row's
`repair`, `answer` or `keep` (`operator_decide.GREEN_LIGHT_OUTCOMES`,
DRE-9051), that is not held and whose action is `decide`, `claim` posts
`operator_decide.receipt_text(decision, now)` through the act registry's
receipt writer, as the act `operator-agent`. It refuses a `do` or held
decision (exit 1) and any other action (exit 3: a receipt already stands),
and posts nothing. When `operator_decide.newest_receipt` over the card's whole
thread is a pipeline-authored receipt carrying the decision's fingerprint,
the pass is spent: nothing is posted, exit 3.

The one exception is the re-keep. When that standing receipt is a `keep` and
`operator_decide.keep_stands` is False — a comment came after it — a Green
Light decision is claimed again, whichever of the three it is, and its fresh
receipt is the newest record on the thread. So a `keep` row is re-read once
per comment it gets, never every pass. Every other standing receipt, and
every waiting decision, is spent by the fingerprint alone.

## apply — one write, under the receipt, off a live read

`apply` acts only when the newest pipeline receipt on the thread reads back,
through `operator_decide.decision_from_receipt`, with the decision's
fingerprint and outcome, and not when the relay's lane guard returned the card
after it (`operator_decide.guard_returned`). It refuses a `do` or held
decision, and an action other than `decide` or `apply-only`. Each of those
writes nothing and exits 1. Then, for every outcome but `keep`, it re-reads
the card; a card no longer in the lane the decision names writes nothing, the
log says why, and it exits 0.

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
* `repair` and `answer` (`operator_decide.MOVING_OUTCOMES`) — a card still in
  Green Light moves to `operator_decide.GREEN_LIGHT_DESTINATION`, Planning,
  through `linear_ops.cmd_state(card, …, expect=(<its lane>,))`, and the log
  says `operator-agent: <CARD> moved — Green Light → Planning`. A card that
  already left Green Light — the CEO answered it, or another writer moved it
  — moves nothing: `operator-agent: <CARD> not moved — it is in <lane> now`,
  exit 0.
* `keep` — no write and no live read: the receipt is the whole of it. The
  log says `operator-agent: <CARD> kept — it stays in Green Light with the
  CEO`, exit 0. The card already holds the CEO's question, so `keep` never
  escalates, never asks one and never moves the card.

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

## A `do` card: claim, receipt and close (DRE-8925)

A `do` step runs commands that do not repeat safely, so it gets three
commands of its own. Each posts through the same `_post`, as the act
`operator-agent`, the comments DRE-8923's composers write, and each reads the
thread the way `select` does (`operator_agent.do_standing`): the newest
pipeline-authored claim, receipt or close-failure line over the card's
fingerprint decides. A row whose outcome is not `do`, or that is held, is
refused (exit 1); a row whose action is not `decide` or `continue` is a pass
already spent or running (exit 3). A comment Linear did not take prints
`refusal_line(card, "do", …)` and exits 4.

* `do-claim` posts `operator_decide.do_claim_text` with the check file's
  `command_lines` (`operator_do.py check`, DRE-8924), for a `decide` row with
  no `do` comment standing, or a `continue` row whose newest is a
  `continues:` receipt that has come due. Anything else: nothing posted, exit
  3. A check file that is not `ok` claims nothing (exit 1): a refused plan
  runs nothing. The workflow runs no command unless this exited 0 (DRE-8928).
* `do-post` posts `operator_decide.do_receipt_text`, its ending chosen here,
  first match: `failed: the run wrote no transcript — …` when the transcript
  is missing or unreadable; `failed:` when it failed (a refused plan's reads
  `failed: `check` — exit 1; …`); `continues:` when the plan says so; else
  `close: <rule>` from `operator_do_ops.close_rule` over the card's title and
  description, read from Linear — `close: none named — …` for `none`. A dry
  run posts nothing (exit 0). It posts only after this pass's claim, or for a
  refused plan, which has none; a `do` receipt or close-failure line already
  standing is a spent pass (exit 3), and no claim at all is exit 1. A receipt
  that did not land leaves the claim standing, so the next pass reads the card
  as `spent` and never runs the step again — the claim's last line says so.
* `do-close` reads the card and its whole thread through the read door,
  `bureau_read.cards([card], max_age=DISPATCH_CARDS_MAX_AGE, comments="all",
  relations=False)`, as `agent_task_snapshot.py` does, and from Linear in any
  other mode or on a `ReadUnknown` (the client prints its `read-fallback:`
  line). It acts only when the newest `do` comment over the card's current
  fingerprint is a receipt ending `close: record-outcome`, `card-done` or
  `own-pr-merge`, with no `GUARD_RETURN_TAG` comment after it, and the card
  is not in Done; otherwise `operator-agent: <CARD> close — nothing owed`,
  exit 0. `own-pr-merge` first reads the receipt's `pull request:` with `gh
  pr view <url> --json state,mergedAt`: merged closes; open prints `close
  waits — pull request <url> is open` and exits 0 until `pr_wait_days`
  (`config/operator-do.json`) after the receipt. The close itself: a
  `no-code` card through the console's outcome door, `console_outcome.record`,
  with `<rule>: <step>` and the receipt comment's URL — the console writes
  its own `Outcome:` record, so the lane guard never returns it, which a bare
  move to Done (and `linear_ops.cmd_card_done`, which leaves a `no-code` card
  alone) would not give; any other card by `linear_ops.cmd_state(card, "Done",
  expect=(<its lane>,))`. Either prints `operator-agent: <CARD> closed —
  <rule> · <console|state>`.

A close that may land on a later pass — the door unreachable, no
`BUREAU_READ_URL` or OIDC token, a 401, 403, 404 or 5xx, a pull request read
that failed, a `cmd_state` that answered False — writes nothing and exits 4:
the receipt stands, so the next pass's `select` reads the card as
`close-only` and hands it here again. A close that never can — the door's 400
or 409, a pull request closed unmerged or open past `pr_wait_days`, a receipt
naming no pull request — posts one `do-close` line, `failed: <what> — the
card is left where it was`, and exits 0: the card is `spent` from then on and
waits for a person. Nothing here ever writes an `Outcome:` line itself.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bureau_read  # noqa: E402
import console_outcome  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import operator_agent  # noqa: E402 — `do_standing`, the reader `select` uses
import operator_decide  # noqa: E402
import operator_do_ops  # noqa: E402
import operator_rules  # noqa: E402
import pipeline_act  # noqa: E402
import planning_escalation  # noqa: E402

EXIT_OK = 0
EXIT_UNFINISHED = 1
EXIT_SPENT = 3
EXIT_REFUSED = 4

DONE_LANE = "Done"
#: The outcomes that write. `do` is a person's step, and writes nothing.
WRITTEN = ((operator_decide.STUDIO, operator_decide.CEO, operator_decide.DONE_ALREADY)
           + operator_decide.GREEN_LIGHT_OUTCOMES)
CLAIMED = ("decide",)
APPLIED = ("decide", "apply-only")

#: The actions under which a `do` card's step runs this pass (DRE-8923).
DO_RUNS = (operator_agent.DECIDE, operator_agent.CONTINUE)
NO_CODE = "no-code"
#: The one entry a refused plan's transcript holds (DRE-8924).
CHECK_COMMAND = "check"

_CARD_QUERY = """query($id: String!) { issue(id: $id) {
     identifier title description state { name } labels { nodes { name } }
   } }"""
#: The receipt's own URL, for the console's record: neither the door nor
#: `comment_records` carries one. The newest hundred comments hold it.
_COMMENT_URLS_QUERY = """query($id: String!) { issue(id: $id) {
     comments(first: 100) { nodes { body createdAt url } }
   } }"""

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


def _rekept(decision, standing, records) -> bool:
    """True for the re-keep: a Green Light decision over a standing `keep`
    that a comment came after, so `keep_stands` no longer holds. Every other
    standing receipt, and every waiting decision, is spent by the
    fingerprint alone."""
    return (decision.outcome in operator_decide.GREEN_LIGHT_OUTCOMES
            and standing.outcome == operator_decide.KEEP
            and not operator_decide.keep_stands(records, decision.fingerprint))


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
    if (standing is not None and standing.fingerprint == decision.fingerprint
            and not _rekept(decision, standing, records)):
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


def _moved(card: str, outcome: str, lane: str) -> int:
    """A `repair` or `answer`: the card back to the lane whose own run re-does
    the work, from Green Light only and from the lane it read."""
    if not _same_lane(lane, operator_agent.GREEN_LIGHT):
        _say(card, f"not moved — it is in {lane or 'no lane'} now")
        return EXIT_OK
    what = f"the move to {operator_decide.GREEN_LIGHT_DESTINATION}"
    try:
        moved = linear_ops.cmd_state(card, operator_decide.GREEN_LIGHT_DESTINATION,
                                     expect=(lane,))
    except Exception as exc:  # noqa: BLE001 — a write that raised did not land
        return _refused(card, outcome, f"{what} ({exc})")
    if not moved:
        return _refused(card, outcome, what)
    _say(card, f"moved — {operator_agent.GREEN_LIGHT} → {operator_decide.GREEN_LIGHT_DESTINATION}")
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
    if decision.outcome == operator_decide.KEEP:
        _say(card, f"kept — it stays in {operator_agent.GREEN_LIGHT} with the CEO")
        return EXIT_OK
    issue = linear_ops.get_issue(card, fresh=True)
    lane = _lane(issue)
    if decision.outcome in operator_decide.MOVING_OUTCOMES:
        return _moved(card, decision.outcome, lane)
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
# a do card: do-claim, do-post, do-close (DRE-8925)                            #
# --------------------------------------------------------------------------- #


def _read_json(path: str):
    """The JSON at `path`, or None when it is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _do_row(card: str, decisions: str, verb: str) -> tuple:
    """`(decision, action, exit)`: the card's `do` row, or the exit a row
    that is none says, with its line printed."""
    decision, action = read_decision(decisions, card)
    if decision is None:
        _say(card, f"{verb} — {decisions} holds no decision for it. Nothing posted")
        return None, None, EXIT_UNFINISHED
    if decision.outcome != operator_decide.DO or decision.held:
        why = f"held: {decision.held}" if decision.held else f"its outcome is {decision.outcome}"
        _say(card, f"{verb} — {why}, not do. Nothing posted")
        return None, None, EXIT_UNFINISHED
    if action not in DO_RUNS:
        _say(card, f"{verb} — its action is {action}: its pass is spent or running. "
                   "Nothing posted")
        return None, None, EXIT_SPENT
    return decision, action, None


def _do_comments(records, fingerprint: str) -> list:
    """The pipeline's claims, receipts and close-failure lines over
    `fingerprint`, oldest first — the comments `select` reads."""
    return [r for r in records or [] if isinstance(r, dict) and r.get("authored_by_pipeline")
            and operator_decide.do_word(r) and operator_decide.do_fingerprint(r) == fingerprint]


def do_claim(card: str, decisions: str, check: str) -> int:
    decision, action, stop = _do_row(card, decisions, "not claimed")
    if decision is None:
        return stop
    checked = _read_json(check)
    lines = checked.get("command_lines") if isinstance(checked, dict) else None
    if (not isinstance(checked, dict) or checked.get("ok") is not True
            or not isinstance(lines, list) or not lines
            or not all(isinstance(line, str) and line.strip() for line in lines)):
        _say(card, f"not claimed — {check} holds no checked plan, and a refused plan runs "
                   "nothing. Nothing posted")
        return EXIT_UNFINISHED
    records = linear_ops.comment_records(card, whole_thread=True)
    standing = operator_agent.do_standing(records, decision.fingerprint, _now())
    if standing is None or standing[0] != action:
        _say(card, f"not claimed — a do claim or receipt with fingerprint "
                   f"{decision.fingerprint} already stands; this pass is spent or running. "
                   "Nothing posted")
        return EXIT_SPENT
    try:
        _post(card, operator_decide.do_claim_text(decision, lines, _now()))
    except Exception as exc:  # noqa: BLE001 — a claim that did not land runs nothing
        return _refused(card, operator_decide.DO, f"the claim ({exc})")
    _say(card, f"do-claim — {len(lines)} command(s) claimed")
    return EXIT_OK


def _is_refusal(transcript) -> bool:
    """True for a refused plan's transcript: its one entry is the check."""
    commands = transcript.get("commands") if isinstance(transcript, dict) else None
    return (isinstance(commands, list) and len(commands) == 1
            and isinstance(commands[0], dict)
            and commands[0].get("command") == CHECK_COMMAND
            and commands[0].get("exit") != 0)


def _ran_failed(transcript: dict) -> bool:
    return bool(transcript.get("failed")) or any(
        isinstance(ran, dict) and ran.get("exit") != 0
        for ran in transcript.get("commands") or [])


def _continues(plan) -> dict | None:
    """The plan's `continues`, as a receipt's ending, or None."""
    said = plan.get("continues") if isinstance(plan, dict) else None
    if not isinstance(said, dict) or not isinstance(said.get("remains"), str):
        return None
    try:
        after = datetime.strptime(str(said.get("after")), "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return {"ending": operator_decide.CONTINUES, "after": after, "remains": said["remains"]}


def _linear_card(card: str) -> dict:
    """The card's title, description, lane and labels, read from Linear."""
    issue = (linear_ops.gql(_CARD_QUERY, {"id": card}) or {}).get("issue")
    if not isinstance(issue, dict):
        raise linear_ops.LinearError(f"{card}: no such card")
    return issue


def _receipt_text(card: str, decision, transcript, plan) -> str:
    """The receipt this pass posts — see the ending's order in the module
    docstring. A transcript the composer cannot read is no transcript."""
    if transcript is not None:
        if _ran_failed(transcript):
            ending = {"ending": operator_decide.FAILED}
        else:
            ending = _continues(plan)
            if ending is None:
                issue = _linear_card(card)
                ending = {"ending": operator_decide.CLOSED, "rule": operator_do_ops.close_rule(
                    issue.get("title"), issue.get("description"))}
        try:
            return operator_decide.do_receipt_text(decision, transcript, ending, _now())
        except ValueError as exc:
            print(f"{operator_decide.LOG_TAG} {card} do-post — the transcript does not read "
                  f"({exc}); posted as no transcript", file=sys.stderr)
    return operator_decide.do_receipt_text(decision, None, {"ending": operator_decide.FAILED},
                                           _now())


def do_post(card: str, decisions: str, transcript_path: str, plan_path: str) -> int:
    decision, action, stop = _do_row(card, decisions, "no receipt")
    if decision is None:
        return stop
    transcript = _read_json(transcript_path)
    if not isinstance(transcript, dict):
        transcript = None
    if transcript is not None and transcript.get("dry_run"):
        _say(card, "do-post — a dry run ran nothing. Nothing posted")
        return EXIT_OK
    records = linear_ops.comment_records(card, whole_thread=True)
    mine = _do_comments(records, decision.fingerprint)
    newest = operator_decide.do_word(mine[-1]) if mine else None
    refusal = _is_refusal(transcript)
    if newest == operator_decide.DO_CLAIM:
        pass
    elif refusal and (operator_agent.do_standing(records, decision.fingerprint, _now())
                      or (None,))[0] == action:
        pass  # a refused plan ran nothing, so no claim stands for it
    elif newest is not None:
        _say(card, f"no receipt — a {newest} comment already stands after the claim; "
                   "this pass is spent. Nothing posted")
        return EXIT_SPENT
    else:
        _say(card, "no receipt — no claim stands for this pass, and the step runs only "
                   "under one. Nothing posted")
        return EXIT_UNFINISHED
    text = _receipt_text(card, decision, transcript, _read_json(plan_path))
    try:
        _post(card, text)
    except Exception as exc:  # noqa: BLE001 — the claim stands; nothing runs again
        return _refused(card, operator_decide.DO, f"the receipt ({exc})")
    _say(card, f"do-post — receipt posted, ending {text.splitlines()[-1]}")
    return EXIT_OK


# --- do-close ------------------------------------------------------------------


def _records(nodes, me) -> list:
    """Comment nodes, oldest first, as `linear_ops.comment_records` writes them."""
    return [{"body": n.get("body") or "",
             "authored_by_pipeline": bool(me) and ((n.get("user") or {}).get("id")) == me,
             "created_at": n.get("createdAt") or None} for n in nodes]


def _read_close(card: str) -> tuple:
    """`(issue, records)`: the card and its whole thread — through the read
    door in mode `on`, else, and on a `ReadUnknown` (which the door's client
    names on its `read-fallback:` line), from Linear."""
    if bureau_read.mode() == "on":
        try:
            read = bureau_read.cards([card], max_age=bureau_read.DISPATCH_CARDS_MAX_AGE,
                                     comments="all", relations=False)
        except bureau_read.ReadUnknown:
            pass
        else:
            node = read.nodes[0]
            # The authorship fact is this key's, never the door's (the
            # console's key): `linear_ops.comment_records`' rule.
            return node, _records(linear_ops.window_nodes(node.get("comments")),
                                  linear_ops.viewer_id())
    return _linear_card(card), linear_ops.comment_records(card, whole_thread=True)


def _labels(issue: dict) -> list:
    return [(n or {}).get("name") or "" for n in ((issue.get("labels") or {}).get("nodes") or [])]


def _owed(card: str, issue: dict, records) -> tuple:
    """`(receipt, ending)` when the newest `do` comment over the card's
    current fingerprint is a receipt naming a close the agent makes, no
    lane-guard return follows it and the card is not in Done; else None."""
    fingerprint = operator_decide.fingerprint(issue)
    mine = _do_comments(records, fingerprint)
    if not mine or _same_lane(_lane(issue), DONE_LANE):
        return None
    receipt = mine[-1]
    ending = operator_decide.do_ending_from_receipt(receipt.get("body"))
    if (ending is None or ending["word"] != operator_decide.DO
            or ending["ending"] != operator_decide.CLOSED
            or ending["rule"] not in operator_decide.CLOSING_RULES):
        return None
    if operator_decide.guard_returned(records, receipt):
        return None
    return receipt, ending


def _pull_request(url: str) -> dict:
    """`gh pr view <url> --json state,mergedAt`, on the job's read-only token."""
    done = subprocess.run(["gh", "pr", "view", url, "--json", "state,mergedAt"],
                          capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or "").strip()[:200]
                           or f"gh exited {done.returncode}")
    said = json.loads(done.stdout)
    if not isinstance(said, dict):
        raise RuntimeError("gh answered no pull request")
    return said


def _receipt_url(card: str, receipt: dict) -> str | None:
    data = linear_ops.gql(_COMMENT_URLS_QUERY, {"id": card}) or {}
    nodes = (((data.get("issue") or {}).get("comments")) or {}).get("nodes") or []
    stamp = receipt.get("created_at")
    for node in nodes:
        if node.get("body") == receipt.get("body") and (
                not stamp or node.get("createdAt") == stamp):
            return node.get("url") or None
    return None


def _close_failed(card: str, decision, what: str) -> int:
    """The one `do-close` line a close that can never land gets."""
    try:
        _post(card, operator_decide.do_close_failed_text(decision, what, _now()))
    except Exception as exc:  # noqa: BLE001 — not posted: the next pass reads it again
        return _refused(card, operator_decide.DO, f"the close-failure line ({exc})")
    _say(card, f"close failed — {what}; left for a person")
    return EXIT_OK


def _close_through_console(card: str, rule: str, decision, receipt: dict) -> int:
    try:
        url = _receipt_url(card, receipt)
    except Exception as exc:  # noqa: BLE001 — a read that failed may answer next pass
        return _refused(card, operator_decide.DO, f"the receipt's URL ({exc})")
    if not url:
        return _refused(card, operator_decide.DO, "the receipt's URL — Linear named none")
    answer = console_outcome.record(card, f"{rule}: {decision.step}", url)
    if answer.recorded:
        _say(card, f"closed — {rule} · console")
        return EXIT_OK
    if answer.never:
        return _close_failed(card, decision, f"the console refused the outcome "
                                             f"({answer.status}: {answer.detail or 'no body'})")
    return _refused(card, operator_decide.DO, f"the outcome through the console "
                                              f"({answer.reason})")


def _close_by_state(card: str, rule: str, lane: str) -> int:
    what = f"the move to {DONE_LANE}"
    try:
        moved = linear_ops.cmd_state(card, DONE_LANE, expect=(lane,))
    except Exception as exc:  # noqa: BLE001 — a write that raised did not land
        return _refused(card, operator_decide.DO, f"{what} ({exc})")
    if not moved:
        return _refused(card, operator_decide.DO, what)
    _say(card, f"closed — {rule} · state")
    return EXIT_OK


def do_close(card: str) -> int:
    issue, records = _read_close(card)
    owed = _owed(card, issue, records)
    if owed is None:
        _say(card, "close — nothing owed")
        return EXIT_OK
    receipt, ending = owed
    rule, lane = ending["rule"], _lane(issue)
    decision = operator_decide.do_decision_from_receipt({"card": card, "lane": lane}, receipt)
    if rule == "own-pr-merge":
        url = ending.get("pull_request")
        if not url:
            return _close_failed(card, decision, "the receipt names no pull request to wait on")
        try:
            said = _pull_request(url)
        except Exception as exc:  # noqa: BLE001 — a read that failed may answer next pass
            return _refused(card, operator_decide.DO, f"the read of pull request {url} ({exc})")
        state = str(said.get("state") or "").upper()
        if not (said.get("mergedAt") or state == "MERGED"):
            if state == "CLOSED":
                return _close_failed(card, decision, f"pull request {url} was closed unmerged")
            days = operator_do_ops.load()["pr_wait_days"]
            at = operator_agent._when(receipt.get("created_at"))
            if at is not None and _now() - at >= timedelta(days=days):
                return _close_failed(card, decision, f"pull request {url} is still open "
                                                     f"{days} days after the receipt")
            _say(card, f"close waits — pull request {url} is open")
            return EXIT_OK
    if NO_CODE in _labels(issue):
        return _close_through_console(card, rule, decision, receipt)
    return _close_by_state(card, rule, lane)


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


def _cmd_do_claim(args) -> int:
    try:
        return do_claim(args.card, args.decisions, args.check)
    except Exception as exc:  # noqa: BLE001 — unread: no claim stands, nothing runs
        print(f"ERROR: {args.card}: the claim could not be made ({exc})", file=sys.stderr)
        return EXIT_UNFINISHED


def _cmd_do_post(args) -> int:
    try:
        return do_post(args.card, args.decisions, args.transcript, args.plan)
    except Exception as exc:  # noqa: BLE001 — a read failed before the post
        print(f"ERROR: {args.card}: the receipt could not be made ({exc}); the claim "
              "stands", file=sys.stderr)
        return EXIT_UNFINISHED


def _cmd_do_close(args) -> int:
    try:
        return do_close(args.card)
    except Exception as exc:  # noqa: BLE001 — a read failed before any write
        print(refusal_line(args.card, operator_decide.DO, f"the close's read ({exc})"))
        return EXIT_REFUSED


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

    dc = sub.add_parser("do-claim", help="post a do card's claim before its step runs")
    dc.add_argument("card")
    dc.add_argument("--decisions", required=True)
    dc.add_argument("--check", required=True)
    dc.set_defaults(fn=_cmd_do_claim)

    dp = sub.add_parser("do-post", help="post a do card's receipt after its step ran")
    dp.add_argument("card")
    dp.add_argument("--decisions", required=True)
    dp.add_argument("--transcript", required=True)
    dp.add_argument("--plan", required=True)
    dp.set_defaults(fn=_cmd_do_post)

    dx = sub.add_parser("do-close", help="make the close a do card's receipt names")
    dx.add_argument("card")
    dx.set_defaults(fn=_cmd_do_close)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
