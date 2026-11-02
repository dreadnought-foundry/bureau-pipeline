#!/usr/bin/env python3
"""The one repair attempt an open console alert gets (DRE-8633, epic DRE-8606).

Alarms fired, and nothing acted on them. This module is the decision half of
the Alarm Repair: it picks which open console alerts get their one repair
attempt, claims each one, and applies what the repair agent decided. It runs
no model: the Alarm Repair workflow (`.github/workflows/alarm-repair.yml`, a
sibling card) runs the agent between `claim` and `apply`.

It is built in the shape of `scripts/triage_repair.py` (DRE-7178). That
module's reusable parts are copied rather than imported, because its `select`
is specific to plan-critic parks: the receipt reader (pipeline-authored, and
opening with the key), `_post` through `pipeline_act.receipt`, and the
`_Writes` / `_Stopped` / `_finish` write ledger, so a failed write leaves an
`unfinished` receipt naming the writes it made.

## Which alerts (`select`)

The alerts are read once, through `bureau_read.open_alerts()`. A read that
fails (`ReadUnknown`) writes `[]` and says so: an alert nobody could read is
never guessed at.

In scope is an alert that has a card, severity `WARN` or `CRITICAL`, and a
`raised_at` at least `ALARM_REPAIR_AFTER_HOURS` (2) hours ago. Every write
this module may make is a write on a card, so an alert whose kind names none
— the overdue `hand-work-overdue` holds among them — is `skip` with the
reason `no card`, and gets no attempt: it reaches the CEO as a row of the
morning briefing's `Open over a day` section once it is a day old.

For each alert in scope, oldest first, the card's WHOLE thread is read
(`linear_ops.comment_records(card, whole_thread=True)`) and the card itself
in one read: lane, labels, description, its `blocks` inverse relations with
their states, and its live hold stamp (`hold.read_stamp`). One action each:

  * `spent` — a contract receipt for this alert's `kind` and `raised_at`
    already stands. Once per raise: an alert resolved and raised again has a
    new `raised_at`, so it gets a new attempt. A receipt-shaped comment from
    any other author grants nothing.
  * `skip` — outside scope: no card, too young, card closed (Done, Canceled
    or Duplicate), or card unreadable.
  * `repair` — the rest.

Each record carries `classes`, computed here and handed to the agent as a
hint — a card may carry several:

  * `prose-blocker` — `prose_blockers.undeclared_claims(card)` is not empty;
    `undeclared` lists those ids.
  * `queued-terminal-blockers` — the card is not an epic, is in `Queued`, has
    at least one `blocks` relation, and every blocker is terminal.
  * `standing-defect` — the alert's kind opens `pipeline_notice:standing-defect:`.
  * `other` — none of the above. The agent still gets its attempt.

`--out` gets every alert's record, and at most `--limit` repair records; a
repair past the limit waits for the next pass. Nothing is written to Linear.

## The claim (`claim`)

`🔩 alarm-repair: started · alert=<kind> · raised=<raised_at>`. The claim IS
the once-per-raise key: a run that dies after it has spent that raise's
attempt. Exit 3, posting nothing, when any receipt for the pair stands.

## The outcome (`apply`)

`apply` acts only while the newest receipt for the pair is `started`, so a
retried or stale run never repeats a write or posts a second outcome. It reads
`<dir>/decision.json`, re-reads the card live, applies the decision, and
posts the finished receipt LAST:

  * `add-relation` — materializes the dependency the card's own prose
    declares, and nothing else. Every id in `relations` must be in the card's
    live `prose_blockers.undeclared_claims`, or the decision is refused. Each
    relation is made with `linear_ops._add_blocked_by`, the seam that turns a
    `**Blocked by:**` line into relations at creation. `repaired`,
    `move=pipeline`. The description, title, labels and lane are never
    written: the sentence is its author's to fix (DRE-2676), and setting the
    relation is the first of the two fixes the sweep's own
    `prose-blocker-no-relation` comment names.
  * `lift-hold` — a lift the hold's own rule says is due and nobody
    performed: `hold.lift_due` answers a lift kind for the live stamp, or the
    stamp's reason is `operator-step` and the card has at least one `blocks`
    relation, every one terminal — `blockers-terminal`, which `lift_due` does
    not decide and the sweep's promotion gate is the only other lifter of
    (DRE-6427). The lift is `hold.lift(card, <kind>, LIFT_BY)`. `repaired`,
    `move=pipeline`. A lift that is not due is refused, and so is a
    `blockers-terminal` one on a card whose relations were not read to the
    end (`prose_blockers.relations_unknown`, DRE-5379): an unread `blocks`
    relation may still be open, so the sweep's promotion gate skips that
    card too.
  * `none` — writes nothing but the receipt. `diagnosed`, with the agent's
    `move` (`operator` or `ceo`).

A refused decision, a missing or malformed `decision.json`, or a failed write
is `unfinished`, `move=operator`: the receipt names the refusal or the failed
write and the writes already made, and `apply` exits non-zero.

The receipt opens with the contract's first line, then says in plain English
the alert's title, the classes, what was done, and the agent's `why` — quoted
inside the untrusted-text fence, because it was written from card text.

## CLI

    alarm_repair.py select --out <file> [--limit N]
    alarm_repair.py claim <CARD> --alert <kind> --raised <raised_at>   exit 3: spent
    alarm_repair.py apply <CARD> --alert <kind> --raised <raised_at> --dir <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bureau_read  # noqa: E402
import hold  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import prose_blockers  # noqa: E402
import sanitize_untrusted  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------------- #
# the contract (identical in every card of DRE-8606)                           #
# --------------------------------------------------------------------------- #

#: The act, and its receipt's opening: a pipeline-authored comment whose first
#: line opens `RECEIPT_KEY` and names an alert's kind and raise is that raise's
#: one attempt.
ACT = "alarm-repair"
RECEIPT_MARK = "🔩"
RECEIPT_KEY = f"{RECEIPT_MARK} {ACT}:"
#: The act's registry tag, which every receipt's trailer carries.
ALARM_REPAIR_TAG = "alarm-repair-receipt"
LOG_PREFIX = "alarm-repair:"

#: An alert is in scope once it has been open this long.
ALARM_REPAIR_AFTER_HOURS = 2
ALARM_REPAIR_MAX_TURNS = 60
DEFAULT_LIMIT = 3
SEVERITIES = ("WARN", "CRITICAL")
#: The receipt's separator: space, U+00B7, space.
SEP = " · "

STARTED = "started"
REPAIRED = "repaired"
DIAGNOSED = "diagnosed"
UNFINISHED = "unfinished"
OUTCOMES = (STARTED, REPAIRED, DIAGNOSED, UNFINISHED)

PIPELINE = "pipeline"
OPERATOR = "operator"
CEO = "ceo"
MOVES = (PIPELINE, OPERATOR, CEO)

# select's actions.
REPAIR = "repair"
SPENT = "spent"
SKIP = "skip"

# The skip reasons.
NO_CARD = "no card"
TOO_YOUNG = "too young"
CARD_CLOSED = "card closed"
CARD_UNREADABLE = "card unreadable"

# The classes.
PROSE_BLOCKER = "prose-blocker"
QUEUED_TERMINAL_BLOCKERS = "queued-terminal-blockers"
STANDING_DEFECT = "standing-defect"
OTHER = "other"
STANDING_DEFECT_PREFIX = "pipeline_notice:standing-defect:"

# The agent's decision.
DECISION_FILE = "decision.json"
ADD_RELATION = "add-relation"
LIFT_HOLD = "lift-hold"
NONE = "none"
DECISIONS = (ADD_RELATION, LIFT_HOLD, NONE)
#: The moves a `none` decision may name.
NONE_MOVES = (OPERATOR, CEO)

#: The lift: `hold.lift(card, <lift kind>, LIFT_BY)`.
LIFT_BY = "scripts/alarm_repair.py"

REPO_MAP_PATH = os.path.join(ROOT, "config", "repo-map.json")
#: The repo a card whose `repo:` label routes nowhere is read in.
HOME_SLUG = "bureau-pipeline"

EXIT_OK = 0
EXIT_UNFINISHED = 1
EXIT_SPENT = 3

FENCE_BEGIN = "===== BEGIN UNTRUSTED CARD TEXT ====="
FENCE_END = "===== END UNTRUSTED CARD TEXT ====="

_CARD = re.compile(r"^DRE-\d+$")
_RECEIPT = re.compile(
    rf"^{re.escape(RECEIPT_KEY)} (?P<outcome>{'|'.join(OUTCOMES)})"
    rf"{re.escape(SEP)}alert=(?P<kind>\S+)"
    rf"{re.escape(SEP)}raised=(?P<raised>\S+)"
    rf"(?:{re.escape(SEP)}move=(?P<move>{'|'.join(MOVES)}))?$")

# The write names an `unfinished` receipt lists, in the order they are made.
W_LIFT = "lift the hold"
W_RECEIPT = "post the receipt"


def _w_relation(blocker: str, card: str) -> str:
    return f"add the relation {blocker} blocks {card}"


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


def parse_receipt(entry) -> dict | None:
    """`{"outcome", "kind", "raised", "move"}` off a receipt's first line, or
    None when it is no receipt or its first line is not the contract's."""
    if not is_receipt(entry):
        return None
    match = _RECEIPT.match(_first_line(_body(entry)))
    return match.groupdict() if match else None


def receipts_for(records, kind: str, raised: str) -> list[dict]:
    """Every contract receipt on the thread for this raise of this alert,
    oldest first, parsed."""
    out = []
    for entry in records or []:
        parsed = parse_receipt(entry)
        if parsed and parsed["kind"] == kind and parsed["raised"] == raised:
            out.append(parsed)
    return out


def card_of(kind: str) -> str | None:
    """The contract's card rule: the last `:`-separated segment of `kind`
    when it is `DRE-<n>`, else None."""
    last = (kind or "").rsplit(":", 1)[-1]
    return last if _CARD.match(last) else None


def _alert_card(alert: dict) -> str | None:
    """The card the door served, when it is a card id."""
    card = alert.get("card")
    return card if isinstance(card, str) and _CARD.match(card) else None


_CARD_QUERY = """query($id: String!) { issue(id: $id) {
     id identifier title description
     state { name }
     labels { nodes { name } }
     parent { identifier }
     children(first: 1) { nodes { id } }
     inverseRelations(first: 50) {
       pageInfo { hasNextPage }
       nodes { type issue { identifier state { name } } }
     }
   } }"""


def read_card(card: str) -> dict:
    """The card with what the classes read off it — lane, labels,
    description, its parent, whether it has children, and its `blocks`
    inverse relations with each blocker's state — in one read. Raises when
    the card is not there."""
    issue = ((linear_ops.gql(_CARD_QUERY, {"id": card}) or {}).get("issue")) or None
    if not issue:
        raise linear_ops.LinearError(f"issue {card} not found")
    return issue


def _labels(card: dict) -> list[str]:
    return [(n or {}).get("name") or "" for n in ((card.get("labels") or {}).get("nodes") or [])]


def _lane(card: dict) -> str:
    return ((card.get("state") or {}).get("name")) or ""


def _is_epic(card: dict) -> bool:
    return bool((card.get("children") or {}).get("nodes"))


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _repo_map() -> dict:
    return {k: v for k, v in _load(REPO_MAP_PATH).items() if not k.startswith("_")}


def _repo(card: dict) -> str:
    """The card's `repo:` label as `owner/name`, through `config/repo-map.json`.
    A card whose label names no repo the map holds — or that carries none —
    is read in bureau-pipeline's."""
    repos = _repo_map()
    for name in _labels(card):
        if name.lower().startswith("repo:"):
            slug = name[len("repo:"):].strip().lower()
            if slug in repos:
                return repos[slug]
    return repos.get(HOME_SLUG) or ""


def blockers(card: dict) -> list[dict]:
    """`[{"card", "state"}]` for every `blocks` relation, by id."""
    return [{"card": ident, "state": state}
            for ident, state in sorted(prose_blockers.blocker_states(card).items())]


def blockers_terminal(card: dict) -> bool:
    """At least one `blocks` relation, and every blocker terminal. False when
    the relations were not read to the end: a blocker past the page may be
    open (DRE-5379)."""
    if prose_blockers.relations_unknown(card):
        return False
    states = prose_blockers.blocker_states(card)
    return bool(states) and all(s in prose_blockers.TERMINAL for s in states.values())


def classes_of(kind: str, card: dict) -> list[str]:
    """The hint `select` hands the agent. Pure."""
    found = []
    if prose_blockers.undeclared_claims(card):
        found.append(PROSE_BLOCKER)
    if (not _is_epic(card) and _lane(card) in lane_contract.QUEUE_LANES
            and blockers_terminal(card)):
        found.append(QUEUED_TERMINAL_BLOCKERS)
    if (kind or "").startswith(STANDING_DEFECT_PREFIX):
        found.append(STANDING_DEFECT)
    return found or [OTHER]


def _when(value) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _record(alert: dict, card: str | None, action: str, why: str,
            issue: dict | None = None, records=None) -> dict:
    kind = alert.get("kind") or ""
    stamp = hold.read_stamp([_body(r) for r in records or []]) if issue is not None else None
    return {
        "card": card,
        "repo": _repo(issue) if issue is not None else "",
        "alert": {"kind": kind, "severity": alert.get("severity"),
                  "title": alert.get("title"), "raised_at": alert.get("raised_at")},
        "classes": classes_of(kind, issue) if issue is not None else [],
        "action": action,
        "why": why,
        "undeclared": (sorted(prose_blockers.undeclared_claims(issue))
                       if issue is not None else []),
        "blockers": blockers(issue) if issue is not None else [],
        "hold": stamp,
        "max_turns": ALARM_REPAIR_MAX_TURNS,
    }


def decide(alert: dict, now: datetime) -> dict:
    """The one action for one open alert, reading its card when it is in
    scope."""
    kind = alert.get("kind") or ""
    card = _alert_card(alert)
    if card is None:
        return _record(alert, None, SKIP, NO_CARD)
    if alert.get("severity") not in SEVERITIES:
        return _record(alert, card, SKIP, f"severity {alert.get('severity')!r} is "
                       f"not one of {', '.join(SEVERITIES)}")
    raised = _when(alert.get("raised_at"))
    if raised is None:
        return _record(alert, card, SKIP, f"{TOO_YOUNG} — its raised_at "
                       f"{alert.get('raised_at')!r} is not an instant")
    if now - raised < timedelta(hours=ALARM_REPAIR_AFTER_HOURS):
        return _record(alert, card, SKIP, f"{TOO_YOUNG} — raised "
                       f"{alert.get('raised_at')}, under {ALARM_REPAIR_AFTER_HOURS} "
                       "hours ago")
    try:
        records = linear_ops.comment_records(card, whole_thread=True)
        issue = read_card(card)
    except Exception as exc:  # noqa: BLE001 — one unread card, never the pass
        return _record(alert, card, SKIP, f"{CARD_UNREADABLE} ({exc})")
    lane = _lane(issue)
    spent = receipts_for(records, kind, alert.get("raised_at") or "")
    if spent:
        return _record(alert, card, SPENT, "this raise has had its one attempt — a "
                       f"`{spent[-1]['outcome']}` receipt stands on the card",
                       issue, records)
    if lane in prose_blockers.TERMINAL:
        return _record(alert, card, SKIP, f"{CARD_CLOSED} — it is in {lane}",
                       issue, records)
    classes = classes_of(kind, issue)
    return _record(alert, card, REPAIR, f"open since {alert.get('raised_at')}, "
                   f"classes {', '.join(classes)}", issue, records)


def select(limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Every open alert, decided, oldest first, with at most `limit` repair
    records. Prints one line per alert."""
    try:
        answer = bureau_read.open_alerts()
    except bureau_read.ReadUnknown as exc:
        print(f"{LOG_PREFIX} open alerts unreadable — {exc}")
        return []
    now = _now()
    alerts = sorted(answer.get("alerts") or [],
                    key=lambda a: (str(a.get("raised_at") or ""), str(a.get("kind") or "")))
    rows: list[dict] = []
    repairs = 0
    for alert in alerts:
        row = decide(alert, now)
        print(f"{LOG_PREFIX} {row['card'] or '(no card)'} → {row['action']} — "
              f"{row['why']} · alert={row['alert']['kind']}")
        if row["action"] == REPAIR:
            if repairs >= max(0, limit):
                print(f"{LOG_PREFIX} {row['card']} — past this pass's limit of {limit} "
                      "repairs; its attempt waits for the next pass")
                continue
            repairs += 1
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# the receipts                                                                 #
# --------------------------------------------------------------------------- #


def started_line(kind: str, raised: str) -> str:
    return f"{RECEIPT_KEY} {STARTED}{SEP}alert={kind}{SEP}raised={raised}"


def finished_line(outcome: str, kind: str, raised: str, move: str) -> str:
    return f"{RECEIPT_KEY} {outcome}{SEP}alert={kind}{SEP}raised={raised}{SEP}move={move}"


def started_body(kind: str, raised: str) -> str:
    return (f"{started_line(kind, raised)}\n\n"
            "The Alarm Repair has claimed this alert's one repair attempt: the repair "
            "agent is reading the card now, and its outcome is posted here when it is "
            "done. This happens once per raise of the alert — a later raise of the same "
            "alert gets an attempt of its own.")


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def _quoted(why: str) -> str:
    """The agent's `why`, inside the untrusted-text fence: it was written from
    card text. A line mimicking a sentinel is defanged first."""
    text = sanitize_untrusted.sanitize_body(str(why or "").strip()) or "(the agent wrote no why)"
    return f"{FENCE_BEGIN}\n{text}\n{FENCE_END}"


def finished_body(outcome: str, kind: str, raised: str, move: str, *, title: str,
                  classes: list[str], done: list[str], why: str | None) -> str:
    """The finished receipt: the contract's first line, then the alert, the
    classes, what was done, and the agent's reasoning, fenced."""
    parts = [
        finished_line(outcome, kind, raised, move),
        f"The alert: {_flat(title)}",
        f"The classes: {', '.join(classes) or OTHER}.",
        "What was done:\n" + ("\n".join(f"- {line}" for line in done)
                              if done else "- nothing"),
    ]
    if why is not None:
        parts.append("The repair agent's reasoning, quoted as it wrote it from the "
                     "card's text:\n\n" + _quoted(why))
    parts.append("This is the one repair attempt this raise of the alert gets.")
    return "\n\n".join(parts)


def _post(card: str, body: str) -> None:
    """Post one receipt through the act registry. Raises on any failure,
    including a comment cap, so a caller never reads a receipt as posted that
    did not land."""
    problem = linear_ops.cmd_comment(card, pipeline_act.receipt("alarm-repair", body))
    if problem:
        raise RuntimeError(problem)


# --------------------------------------------------------------------------- #
# claim                                                                        #
# --------------------------------------------------------------------------- #


def claim(card: str, kind: str, raised: str) -> int:
    records = linear_ops.comment_records(card, whole_thread=True)
    if receipts_for(records, kind, raised):
        print(f"{LOG_PREFIX} {card} — a receipt for alert={kind} raised={raised} already "
              f"stands; this raise's attempt is spent. Exit {EXIT_SPENT}, nothing posted")
        return EXIT_SPENT
    _post(card, started_body(kind, raised))
    print(f"{LOG_PREFIX} {card} → {STARTED} · alert={kind}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# apply                                                                        #
# --------------------------------------------------------------------------- #


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


class _Apply:
    """What one `apply` knows of its alert, for the receipts it posts."""

    def __init__(self, card: str, kind: str, raised: str):
        self.card, self.kind, self.raised = card, kind, raised
        self.title = ""
        self.classes: list[str] = []
        self.why: str | None = None


def _finish(run: _Apply, writes: _Writes, what: str) -> int:
    """Post `unfinished` naming `what` and the writes made, and exit non-zero."""
    card = run.card
    made = ", ".join(writes.made) or "none"
    print(f"ERROR: {card}: {what}. Writes already made: {made}", file=sys.stderr)
    done = [f"refused or stopped: {_flat(what)}", f"Writes already made: {made}"]
    try:
        _post(card, finished_body(UNFINISHED, run.kind, run.raised, OPERATOR,
                                  title=run.title, classes=run.classes, done=done,
                                  why=run.why))
    except Exception as exc:  # noqa: BLE001 — the newest receipt stays `started`
        print(f"ERROR: {card}: the unfinished receipt could not be posted ({exc}); "
              "the newest receipt is still the claim", file=sys.stderr)
    print(f"{LOG_PREFIX} {card} → {UNFINISHED} · alert={run.kind}")
    return EXIT_UNFINISHED


def _claim_stands(records, kind: str, raised: str) -> bool:
    """The newest receipt for this raise of this alert is its `started`."""
    spent = receipts_for(records, kind, raised)
    return bool(spent) and spent[-1]["outcome"] == STARTED


def _title(kind: str, raised: str) -> str:
    """The alert's title off the open-alerts read, or why it is not there."""
    try:
        answer = bureau_read.open_alerts()
    except bureau_read.ReadUnknown as exc:
        return f"{kind} (its title could not be read — {exc})"
    for alert in answer.get("alerts") or []:
        if alert.get("kind") == kind and alert.get("raised_at") == raised:
            return str(alert.get("title") or kind)
    return f"{kind} (no longer open on the console)"


def read_decision(directory: str | None) -> tuple[dict | None, str | None]:
    """`(decision, None)`, or `(None, why it is refused)`. Checks the shape
    only: whether the decision may be applied is read off the live card."""
    path = os.path.join(directory or "", DECISION_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None, f"the repair agent wrote no {DECISION_FILE}"
    try:
        decision = json.loads(text)
    except ValueError as exc:
        return None, f"{DECISION_FILE} is not valid JSON ({exc})"
    if not isinstance(decision, dict):
        return None, f"{DECISION_FILE} is not a JSON object"
    action = decision.get("action")
    if action not in DECISIONS:
        return None, (f"{DECISION_FILE} names the action {_flat(action)[:60]!r}, "
                      f"which is not one of {', '.join(DECISIONS)}")
    if action == ADD_RELATION:
        relations = decision.get("relations")
        if (not isinstance(relations, list) or not relations
                or not all(isinstance(r, str) and _CARD.match(r) for r in relations)):
            return None, (f"{DECISION_FILE}'s add-relation names no relations, or "
                          "one that is not a DRE-<n> id")
    if action == NONE and decision.get("move") not in NONE_MOVES:
        return None, (f"{DECISION_FILE}'s none names the move "
                      f"{_flat(decision.get('move'))[:30]!r}, which is not one of "
                      f"{', '.join(NONE_MOVES)}")
    return decision, None


def lift_kind(stamp: dict | None, card: dict, bodies) -> str | None:
    """The lift the hold's own rule says is due for the live stamp, or None.
    `hold.lift_due` decides every kind but `blockers-terminal`, which is due
    for an `operator-step` stamp on a card whose `blocks` relations are all
    terminal. No pull request is read here, so a `new-head` lift is never
    due."""
    if not stamp:
        return None
    due = hold.lift_due(stamp, lane=_lane(card), labels=_labels(card), pr_head=None,
                        rail_slugs=set(_repo_map()), bodies=bodies)
    if due is None and stamp.get("reason") == hold.OPERATOR_STEP_REASON \
            and blockers_terminal(card):
        due = hold.BLOCKERS_TERMINAL
    return due


def apply(card: str, kind: str, raised: str, directory: str | None) -> int:
    run = _Apply(card, kind, raised)
    writes = _Writes(card)
    try:
        records = linear_ops.comment_records(card, whole_thread=True)
    except Exception as exc:  # noqa: BLE001
        return _finish(run, writes, f"the card's thread could not be read ({exc})")
    if not _claim_stands(records, kind, raised):
        print(f"ERROR: {card}: no claim stands — the newest receipt for alert={kind} "
              f"raised={raised} is not `{STARTED}`, so this attempt is not this run's "
              "to apply. Nothing posted", file=sys.stderr)
        return EXIT_UNFINISHED
    run.title = _title(kind, raised)
    try:
        issue = read_card(card)
    except Exception as exc:  # noqa: BLE001
        return _finish(run, writes, f"the card could not be read ({exc})")
    run.classes = classes_of(kind, issue)
    decision, refused = read_decision(directory)
    if refused is not None:
        return _finish(run, writes, refused)
    run.why = str(decision.get("why") or "")
    action = decision["action"]

    try:
        if action == ADD_RELATION:
            undeclared = prose_blockers.undeclared_claims(issue)
            wanted = list(dict.fromkeys(decision["relations"]))
            outside = [r for r in wanted if r not in undeclared]
            if outside:
                return _finish(run, writes, "add-relation was refused: "
                               f"{', '.join(outside)} is not a dependency the card's "
                               "own prose declares without a relation (its undeclared "
                               f"claims are {', '.join(sorted(undeclared)) or 'none'})")
            for blocker in wanted:
                writes.do(_w_relation(blocker, card),
                          lambda b=blocker: linear_ops._add_blocked_by(issue["id"], [b]))
            done = [f"added the relation: {b} blocks {card}" for b in wanted]
            return _finished(run, writes, REPAIRED, PIPELINE, done)

        if action == LIFT_HOLD:
            bodies = [_body(r) for r in records]
            stamp = hold.read_stamp(bodies)
            if stamp is None:
                return _finish(run, writes, "lift-hold was refused: the card carries "
                               "no live hold stamp")
            due = lift_kind(stamp, issue, bodies)
            if due is None and stamp.get("reason") == hold.OPERATOR_STEP_REASON \
                    and prose_blockers.relations_unknown(issue):
                return _finish(run, writes, "lift-hold was refused: the card's "
                               "relations were not read to the end, so a blocker "
                               "past the page may still be open")
            if due is None:
                return _finish(run, writes, "lift-hold was refused: the "
                               f"`{stamp['reason']}` hold's own rule does not say its "
                               "lift is due")
            writes.do(W_LIFT, lambda: hold.lift(card, due, LIFT_BY, reason=stamp["reason"]))
            done = [f"lifted the hold: {hold.lift_line(stamp['reason'], due, LIFT_BY)}"]
            return _finished(run, writes, REPAIRED, PIPELINE, done)

        return _finished(run, writes, DIAGNOSED, decision["move"], [])
    except _Stopped as stop:
        return _finish(run, writes, stop.what)


def _finished(run: _Apply, writes: _Writes, outcome: str, move: str,
              done: list[str]) -> int:
    """The finished receipt, LAST. A `_Stopped` raised here is `apply`'s."""
    writes.do(W_RECEIPT, lambda: _post(run.card, finished_body(
        outcome, run.kind, run.raised, move, title=run.title, classes=run.classes,
        done=done, why=run.why)))
    print(f"{LOG_PREFIX} {run.card} → {outcome} · alert={run.kind}")
    return EXIT_OK


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
        return claim(args.card, args.alert, args.raised)
    except Exception as exc:  # noqa: BLE001 — unread or unposted: no claim stands
        print(f"ERROR: {args.card}: the claim could not be made ({exc})", file=sys.stderr)
        return EXIT_UNFINISHED


def _cmd_apply(args) -> int:
    return apply(args.card, args.alert, args.raised, args.dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="decide each open alert's attempt; writes nothing")
    s.add_argument("--out", required=True)
    s.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    s.set_defaults(fn=_cmd_select)

    c = sub.add_parser("claim", help="post the started receipt, once per raise")
    c.add_argument("card")
    c.add_argument("--alert", required=True, help="the alert's kind, verbatim")
    c.add_argument("--raised", required=True, help="the alert's raised_at, verbatim")
    c.set_defaults(fn=_cmd_claim)

    a = sub.add_parser("apply", help="apply what the repair agent decided")
    a.add_argument("card")
    a.add_argument("--alert", required=True, help="the alert's kind, verbatim")
    a.add_argument("--raised", required=True, help="the alert's raised_at, verbatim")
    a.add_argument("--dir", required=True)
    a.set_defaults(fn=_cmd_apply)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
