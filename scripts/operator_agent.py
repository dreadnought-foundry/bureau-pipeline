#!/usr/bin/env python3
"""The operator agent's select and decide (DRE-8745, epic DRE-8738).

Every card waiting on a person across the fleet is read in hourly turns, and
each gets at most one decision per pass. This module holds the agent's two
read-only commands, modeled on `triage_repair.py`'s `select`: `select` finds
the cards and only reads, and `decide` turns the model's answers into
decisions, prints one line per card and touches nothing. The receipt that
spends a pass, and the writes that follow it, are `operator_agent_apply.py`'s
`claim` and `apply` (DRE-8746). This module holds no write at all.

## Which cards (`select`)

One board read, `board_snapshot.read_board`, of `LANES`. Every card in
`Hand-work` is taken. In `Queued` or `Intake`, a card labeled `needs-human`
or `operator-step` is taken. In any of the three, a card whose title opens
`[OPERATOR]` or `OPERATOR:` is taken. A card in any other lane already
belongs to another writer and is not read.

The whole fleet: a card whose `repo:` label is a key of
`config/repo-map.json`, read from the file on every run, and a card with no
`repo:` label at all, handed on as bureau-pipeline's, the way triage repair
hands one on. A card whose `repo:` label names no fleet repo is not taken.

Three kinds are left alone: a card whose title opens `PROOF:`, a card wearing
`hand-built`, and a standing card from `config/hand-work-age.json`'s
`standing` list. A card in `config/operator-agent.json`'s `leave_alone` list
— the switch that turns this agent on, and its off-reason — is named once in
the log, `operator-agent: <CARD> left alone — <why>`, and gets no decision, so
the agent never decides or closes the card that turns it on. Each list is
read from its file and never copied.

## Per card

The whole thread, `linear_ops.comment_records(card, whole_thread=True)`; the
card's fingerprint, `operator_decide.fingerprint`; its rule outcome,
`operator_rules.rule_outcome`; and its standing receipt,
`operator_decide.newest_receipt`. A card with no standing receipt is
`decide`. A card with one is read by six rules, first match wins:

  1. a `ceo` receipt, and the card entered `Green Light` after the receipt's
     time: `spent`. The CEO has had the question, and is never asked twice.
  2. a `done-already` receipt, and `operator_decide.guard_returned`: the relay
     lane guard returned the close (DRE-7327). `guard-returned` — the agent
     never closes it again; a person's own comment and close are the record.
  3. a `done-already` receipt, and the card entered `Done` after the
     receipt's time: `spent`. A person reopened it.
  4. the receipt's fingerprint is not the card's: `decide`. The fingerprint
     is the title and description alone (DRE-8744), so a lane move or a
     `hand-step:` label added after the receipt changes nothing.
  5. a `studio` receipt: `spent` when the card is blocked by
     `studio.blocker` (DRE-8582) on the board read's `inverseRelations`, and
     `apply-only` when it is not.
  6. a `ceo` or `done-already` receipt with no such move since:
     `apply-only`. A pass that died between the receipt and its write
     finishes the write on the next pass, without a second receipt.

A `do` decision writes no receipt (DRE-8746), so none of the six names one;
a receipt that matches none of them is `decide`. The lane history is read,
`linear_ops.lane_history`, only for a card whose receipt is `ceo` or
`done-already`. A receipt that does not read back
(`operator_decide.decision_from_receipt` answers None), or a read that
fails, skips the card for this pass, said on stderr.

## For the model, and unread

Of the rows that would be `decide`, at most `--limit` (`DEFAULT_LIMIT`) are
marked `for_model` and keep `decide`; every other one is `unread` and gets no
decision on this pass. First the cards the rules class `ceo` or `studio`,
sorted by card number. Then `slots = max(0, limit - <those marked>)` are
filled from the rest, sorted by card number as a ring of `n`, starting at
`(h × slots) mod n`, where `h` is the whole hours from 1970-01-01 00:00 UTC
to `--hour` (the clock by default), and wrapping round. So consecutive hourly
passes read consecutive windows, and `ceil(n / slots)` hours read every card
of an unchanging set at least once.

## The card file

`<cards-dir>/<CARD>.md` holds the card's title, lane, labels, description and
its `COMMENTS_KEPT` newest comments, each cut at `COMMENT_CHARS` and each with
its author kind and time. Every value goes through
`sanitize_untrusted.py`'s defang, and the file opens and closes with the
untrusted-card-text sentinels. It is written outside this repository — a
directory inside it is refused — and never committed.

## The reads, named

Each pass makes one paged board read (`board_snapshot.read_board`), one
whole-thread read per card taken (`linear_ops.comment_records`), and one
`linear_ops.lane_history` read per card whose receipt is `ceo` or
`done-already`. None goes through `bureau_read.py`'s door, so no
`read-fallback:` line is printed for them: they are direct, not fallbacks.

## The decision (`decide`)

`decide` reads `cards.json` and prints exactly one line per row:

  * `decide`: `operator_decide.decide` is called once, with the card file as
    `card_text` (empty when the file is missing), `operator_rules.
    host_limits_text()` as `as_built`, and `<answers>/<CARD>.choices.json` as
    `choices_file`. A decision is written and printed with
    `operator_decide.log_line`; None prints `unread_line(UNREAD_NO_ANSWER)`.
  * `unread`: never decided, nothing written, `unread_line(UNREAD_LIMIT)`.
  * `spent`, `apply-only`, `guard-returned`: rebuilt with
    `operator_decide.decision_from_receipt(row, row["receipt"])`, written and
    printed with the row's action, so the line ends ` · spent`,
    ` · apply-only` or ` · guard-returned` and never reads as a fresh `do`.

`decisions.json` is a list of `Decision.to_json()` objects, each with the
row's `action` added. `decide` holds no Linear key and makes no Linear call.

## CLI

    operator_agent.py select --out cards.json --cards-dir DIR [--limit N] [--hour H]
        exit 2: the board could not be read · 3: DIR is inside this repository
    operator_agent.py decide --cards cards.json --cards-dir DIR --answers DIR
                             --out decisions.json
        exit 1: a routed row's receipt did not read back
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_snapshot  # noqa: E402
import linear_ops  # noqa: E402
import operator_decide  # noqa: E402
import operator_rules  # noqa: E402
from sanitize_untrusted import sanitize_body, sanitize_line  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

RULES_PATH = str(operator_rules.CONFIG_PATH)
HAND_WORK_AGE_PATH = os.path.join(ROOT, "config", "hand-work-age.json")
REPO_MAP_PATH = os.path.join(ROOT, "config", "repo-map.json")
HOME_SLUG = "bureau-pipeline"

HAND_WORK = "Hand-work"
LANES = ("Intake", "Queued", HAND_WORK)
GREEN_LIGHT = "Green Light"
DONE = "Done"
TAKE_LABELS = ("needs-human", "operator-step")
TITLE_OPENS = ("[OPERATOR]", "OPERATOR:")
PROOF_OPENS = "PROOF:"
HAND_BUILT = "hand-built"

DECIDE, UNREAD, SPENT, APPLY_ONLY, GUARD_RETURNED = (
    "decide", "unread", "spent", "apply-only", "guard-returned")
ACTIONS = (DECIDE, UNREAD, SPENT, APPLY_ONLY, GUARD_RETURNED)
#: The actions whose decision is rebuilt from the standing receipt.
FROM_RECEIPT = (SPENT, APPLY_ONLY, GUARD_RETURNED)
#: The rule outcomes whose cards take the model's slots first.
ROUTED = (operator_rules.CEO, operator_rules.STUDIO)

DEFAULT_LIMIT = 20
COMMENTS_KEPT = 20
COMMENT_CHARS = 4000

BEGIN = "===== BEGIN UNTRUSTED CARD TEXT ====="
END = "===== END UNTRUSTED CARD TEXT ====="

EXIT_OK = 0
EXIT_UNREAD_RECEIPT = 1
EXIT_NO_BOARD = 2
EXIT_INSIDE_REPO = 3

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NUMBER = re.compile(r"^(?P<team>[A-Za-z]+)-(?P<n>\d+)$")
#: What this module prints that is not a per-card line. It never opens with
#: `operator_decide.LOG_TAG`, so the proof record reads only per-card lines
#: under that tag.
_NOTE = "operator agent select:"


# --------------------------------------------------------------------------- #
# the tables, read on every run                                                #
# --------------------------------------------------------------------------- #


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _repo_map() -> dict:
    return {k: v for k, v in _load(REPO_MAP_PATH).items() if not k.startswith("_")}


def _standing() -> set:
    return {row["card"] for row in _load(HAND_WORK_AGE_PATH).get("standing") or []}


# --------------------------------------------------------------------------- #
# which cards                                                                  #
# --------------------------------------------------------------------------- #


def _labels(card: dict) -> list[str]:
    return [(n or {}).get("name") or "" for n in ((card.get("labels") or {}).get("nodes") or [])]


def _lane(card: dict) -> str:
    return (card.get("state") or {}).get("name") or ""


def _title(card: dict) -> str:
    return (card.get("title") or "").lstrip()


def waiting(card: dict) -> bool:
    """In Hand-work; or in Queued or Intake, labeled for a person; or titled
    for the operator in any of the three."""
    lane = _lane(card)
    if lane not in LANES:
        return False
    if lane == HAND_WORK or _title(card).startswith(TITLE_OPENS):
        return True
    return bool(set(_labels(card)) & set(TAKE_LABELS))


def repo_of(card: dict, repos: dict) -> str | None:
    """The card's repo as `owner/name`: its `repo:` label's, bureau-pipeline's
    when it carries none, and None when its label names no fleet repo."""
    named = [n[len("repo:"):].strip().lower() for n in _labels(card)
             if n.lower().startswith("repo:")]
    if not named:
        return repos.get(HOME_SLUG)
    return next((repos[slug] for slug in named if slug in repos), None)


def left_kind(card: dict, standing: set) -> bool:
    """A `PROOF:` card, a `hand-built` card or a standing card."""
    return (_title(card).startswith(PROOF_OPENS) or HAND_BUILT in _labels(card)
            or (card.get("identifier") or "") in standing)


# --------------------------------------------------------------------------- #
# spent, apply-only, guard-returned or decide                                  #
# --------------------------------------------------------------------------- #


def _when(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def entered_since(history, lane: str, receipt: dict) -> bool:
    """True when the card entered `lane` after the receipt's time. A receipt
    or an entry with no time counts the entry: the agent writes nothing more
    on a card it cannot place, never a second time."""
    at = _when(receipt.get("created_at"))
    for entry in history or []:
        if ((entry.get("toState") or {}).get("name") or "") != lane:
            continue
        when = _when(entry.get("createdAt"))
        if at is None or when is None or when > at:
            return True
    return False


def blocked_by(card: dict, blocker: str) -> bool:
    """True when the board read shows `blocker` blocking the card."""
    return any(rel.get("type") == "blocks"
               and ((rel.get("issue") or {}).get("identifier") or "") == blocker
               for rel in (card.get("inverseRelations") or {}).get("nodes") or [])


def standing_action(card: dict, fingerprint: str, records, receipt: dict,
                    recorded: operator_decide.Decision, history, rules: dict) -> str:
    """The action for a card under a standing receipt — see the module
    docstring's six rules. `history` is the lane history, read only for a
    `ceo` or `done-already` receipt."""
    outcome = recorded.outcome
    if outcome == operator_decide.CEO and entered_since(history, GREEN_LIGHT, receipt):
        return SPENT
    if outcome == operator_decide.DONE_ALREADY:
        if operator_decide.guard_returned(records, receipt):
            return GUARD_RETURNED
        if entered_since(history, DONE, receipt):
            return SPENT
    if recorded.fingerprint != fingerprint:
        return DECIDE
    if outcome == operator_decide.STUDIO:
        return SPENT if blocked_by(card, rules["studio"]["blocker"]) else APPLY_ONLY
    if outcome in (operator_decide.CEO, operator_decide.DONE_ALREADY):
        return APPLY_ONLY
    return DECIDE


# --------------------------------------------------------------------------- #
# for the model, and unread                                                    #
# --------------------------------------------------------------------------- #


def _id_key(row: dict) -> tuple:
    found = _NUMBER.match(row.get("card") or "")
    return (found.group("team"), int(found.group("n")), "") if found else ("", 0, row["card"])


def hours_since_epoch(hour: datetime) -> int:
    if hour.tzinfo is None:
        hour = hour.replace(tzinfo=UTC)
    return int((hour - _EPOCH).total_seconds() // 3600)


def mark(rows: list[dict], limit: int, hour: datetime) -> None:
    """Mark at most `limit` of the rows that would be `decide` for the model,
    in place: the rule-routed cards first, then an hourly window of the ring
    of the rest. Every other such row is `unread`."""
    pending = [r for r in rows if r["action"] in (DECIDE, UNREAD)]
    for row in pending:
        row["action"], row["for_model"] = UNREAD, False
    routed = sorted((r for r in pending if (r.get("rule") or {}).get("outcome") in ROUTED),
                    key=_id_key)
    chosen = routed[:max(0, limit)]
    taken = {id(r) for r in routed}
    rest = sorted((r for r in pending if id(r) not in taken), key=_id_key)
    slots, n = max(0, limit - len(chosen)), len(rest)
    if n and slots:
        start = (hours_since_epoch(hour) * slots) % n
        chosen += [rest[(start + i) % n] for i in range(min(slots, n))]
    for row in chosen:
        row["action"], row["for_model"] = DECIDE, True


# --------------------------------------------------------------------------- #
# the card file                                                                #
# --------------------------------------------------------------------------- #


def card_file(row: dict, records) -> str:
    """The card as the model reads it, fenced and defanged."""
    kept = list(records or [])[-COMMENTS_KEPT:]
    lines = [BEGIN,
             f"Card: {sanitize_line(row['card'])}",
             f"Title: {sanitize_line(row['title'])}",
             f"Lane: {sanitize_line(row['lane'])}",
             f"Labels: {sanitize_line(', '.join(row['labels']))}",
             f"Repo: {sanitize_line(row['repo'])}",
             "",
             "Description:",
             sanitize_body(row["description"]),
             "",
             f"Comments — the {len(kept)} newest, oldest first:"]
    for n, record in enumerate(kept, 1):
        kind = "pipeline" if record.get("authored_by_pipeline") else "person"
        when = record.get("created_at") or "no time given"
        lines += ["",
                  sanitize_line(f"--- comment {n} · by a {kind} · {when}"),
                  sanitize_body((record.get("body") or "")[:COMMENT_CHARS])]
    lines.append(END)
    return "\n".join(lines) + "\n"


def _card_path(cards_dir: str, card: str) -> str:
    return os.path.join(cards_dir, f"{card}.md")


# --------------------------------------------------------------------------- #
# select                                                                       #
# --------------------------------------------------------------------------- #


class BoardUnread(RuntimeError):
    """The board read failed: no file is written, because an unread board is
    not an empty one (DRE-2034)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _skip(ident: str, why: str) -> None:
    print(f"{_NOTE} {ident} — {why}; skipped this pass", file=sys.stderr)


def select(cards_dir: str, limit: int = DEFAULT_LIMIT, hour: datetime | None = None
           ) -> list[dict]:
    """Every card waiting on a person, read and classed, in card-number order.
    Writes each card's file to `cards_dir` and nothing to Linear."""
    rules = operator_rules.load_rules(RULES_PATH)
    repos, standing = _repo_map(), _standing()
    leave_alone = {row["card"]: row["why"] for row in rules["leave_alone"]}
    try:
        cards, _requests = board_snapshot.read_board(LANES)
    except Exception as exc:  # noqa: BLE001 — an unread board is not an empty board
        raise BoardUnread(str(exc)) from exc
    os.makedirs(cards_dir, exist_ok=True)

    rows: list[dict] = []
    for card in sorted(cards, key=lambda c: _id_key({"card": c.get("identifier") or ""})):
        ident = card.get("identifier") or ""
        if not waiting(card):
            continue
        repo = repo_of(card, repos)
        if repo is None or left_kind(card, standing):
            continue
        if ident in leave_alone:
            print(f"{operator_decide.LOG_TAG} {ident} left alone — {leave_alone[ident]}")
            continue
        row = {"card": ident, "repo": repo, "lane": _lane(card),
               "title": card.get("title") or "", "labels": _labels(card),
               "description": card.get("description") or ""}
        try:
            records = linear_ops.comment_records(ident, whole_thread=True)
        except Exception as exc:  # noqa: BLE001 — one unread card, never the pass
            _skip(ident, f"could not read its thread ({exc})")
            continue
        fingerprint = operator_decide.fingerprint(row)
        match = operator_rules.rule_outcome(row["title"], row["description"], rules)
        receipt = operator_decide.newest_receipt(records)
        action, body = DECIDE, None
        if receipt is not None:
            recorded = operator_decide.decision_from_receipt(row, receipt.get("body"), rules)
            if recorded is None:
                _skip(ident, "its standing receipt does not read back")
                continue
            history = None
            if recorded.outcome in (operator_decide.CEO, operator_decide.DONE_ALREADY):
                try:
                    history = linear_ops.lane_history(ident)
                except Exception as exc:  # noqa: BLE001 — one unread card, never the pass
                    _skip(ident, f"could not read its lane history ({exc})")
                    continue
            action = standing_action(card, fingerprint, records, receipt, recorded,
                                     history, rules)
            if action in FROM_RECEIPT:
                body = receipt.get("body")
        row.update(fingerprint=fingerprint,
                   rule=dataclasses.asdict(match) if match is not None else None,
                   for_model=action == DECIDE, action=action, receipt=body)
        with open(_card_path(cards_dir, ident), "w", encoding="utf-8") as fh:
            fh.write(card_file(row, records))
        rows.append(row)

    mark(rows, limit, hour or _now())
    counts = {a: sum(1 for r in rows if r["action"] == a) for a in ACTIONS}
    print(f"{_NOTE} {len(rows)} card(s) taken — "
          + ", ".join(f"{counts[a]} {a}" for a in ACTIONS), file=sys.stderr)
    return rows


# --------------------------------------------------------------------------- #
# decide                                                                       #
# --------------------------------------------------------------------------- #


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def decide(rows: list[dict], cards_dir: str, answers_dir: str) -> tuple[list[dict], bool]:
    """At most one decision per row, and one printed line per row. The
    decisions, and whether every routed row's receipt read back."""
    rules = operator_rules.load_rules(RULES_PATH)
    as_built = operator_rules.host_limits_text(rules)
    decisions, whole = [], True
    for row in rows:
        ident, action = row["card"], row["action"]
        if action == UNREAD:
            print(operator_decide.unread_line(ident, operator_decide.UNREAD_LIMIT))
            continue
        if action in FROM_RECEIPT:
            decision = operator_decide.decision_from_receipt(row, row.get("receipt"), rules)
            if decision is None:
                print(f"ERROR: {ident} is {action} but its receipt does not read back — "
                      "no decision this pass", file=sys.stderr)
                whole = False
                continue
        else:
            answer = operator_decide.read_answer(os.path.join(answers_dir, f"{ident}.json"))
            decision = operator_decide.decide(
                row, answer, _read_text(_card_path(cards_dir, ident)), as_built=as_built,
                rules=rules, choices_file=os.path.join(answers_dir, f"{ident}.choices.json"))
            if decision is None:
                print(operator_decide.unread_line(ident, operator_decide.UNREAD_NO_ANSWER))
                continue
        decisions.append({**decision.to_json(), "action": action})
        print(operator_decide.log_line(decision, action))
    return decisions, whole


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _hour(value: str | None) -> datetime | None:
    if value is None:
        return None
    when = _when(value)
    if when is None:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO time")
    return when


def _write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _cmd_select(args) -> int:
    if board_snapshot.inside_repo(args.cards_dir):
        print(f"ERROR: {args.cards_dir} is inside this repository — a card file is "
              "written to the runner's temp directory and never committed", file=sys.stderr)
        return EXIT_INSIDE_REPO
    try:
        rows = select(args.cards_dir, args.limit, args.hour)
    except BoardUnread as exc:
        print(f"ERROR: the board could not be read ({exc})", file=sys.stderr)
        return EXIT_NO_BOARD
    _write_json(args.out, rows)
    return EXIT_OK


def _cmd_decide(args) -> int:
    with open(args.cards, encoding="utf-8") as fh:
        rows = json.load(fh)
    decisions, whole = decide(rows, args.cards_dir, args.answers)
    _write_json(args.out, decisions)
    return EXIT_OK if whole else EXIT_UNREAD_RECEIPT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="read the cards waiting on a person; writes nothing")
    s.add_argument("--out", required=True)
    s.add_argument("--cards-dir", required=True)
    s.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    s.add_argument("--hour", type=_hour, default=None,
                   help="an ISO time; the clock by default")
    s.set_defaults(fn=_cmd_select)

    d = sub.add_parser("decide", help="one decision per card from the model's answers")
    d.add_argument("--cards", required=True)
    d.add_argument("--cards-dir", required=True)
    d.add_argument("--answers", required=True)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=_cmd_decide)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
