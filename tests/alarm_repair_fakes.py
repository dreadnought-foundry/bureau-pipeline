"""The test board for the Alarm Repair (DRE-8633, epic DRE-8606).

`AlarmRepairBoard` follows `TriageRepairBoard`: it holds each card's lane,
labels, description, relations and comments, serves the open-alerts read, and
answers the calls `alarm_repair`, `hold`, `prose_blockers` and
`linear_ops._add_blocked_by` make FROM that state. A write changes the state,
and the next read sees it, so a scenario test asserts the board's end state —
the relations a card carries, its hold, its newest comment — not only which
functions were called. The real `linear_ops._add_blocked_by` runs over it,
reaching `get_issue` and the relation mutation here.

    board = AlarmRepairBoard()
    board.add("DRE-7909", lane="Queued", relations=["DRE-7300", "DRE-7301"])
    board.alert("pipeline_stuck:DRE-7909", raised_at=..., severity="CRITICAL")
    with board.patched():
        alarm_repair.main(["select", "--out", str(out)])

`board.events` is every write, in the order it was made, as `(kind, card,
detail)`. `board.fail(kind, …)` makes the next write of that kind raise, the
way a Linear error does; `board.unreadable(reason)` makes the open-alerts read
raise `bureau_read.ReadUnknown`.
"""
from __future__ import annotations

import contextlib
import copy
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402

#: The fleet user: every comment the pipeline writes is this user's, and
#: `comment_records` says `authored_by_pipeline` for exactly these.
FLEET_USER = "user-agent-bureau"
#: Anybody else with comment access.
PERSON_USER = "user-a-person"

_START = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)
_CARD = re.compile(r"^DRE-\d+$")


class BoardWriteFailed(linear_ops.LinearError):
    """A write the test told the board to refuse."""


@dataclass
class Comment:
    body: str
    created_at: str
    user: str = FLEET_USER


@dataclass
class Card:
    identifier: str
    title: str
    lane: str
    labels: list[str]
    description: str
    #: Every card that BLOCKS this one — its `blocks` inverse relations, each
    #: read with the blocker's live lane when the blocker is on the board.
    relations: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    parent: str | None = None
    #: True when the read's relation page did not reach the end — the card
    #: holds more relations than `relations` (DRE-5379).
    relations_unread: bool = False


def iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**delta) -> str:
    """An ISO-8601 UTC instant `delta` before now — `ago(hours=3)`."""
    return iso(datetime.now(UTC) - timedelta(**delta))


class AlarmRepairBoard:
    def __init__(self) -> None:
        self.cards: dict[str, Card] = {}
        self.alerts: list[dict] = []
        self.events: list[tuple[str, str, str]] = []
        self.reads: list[tuple[str, str]] = []
        self._failures: dict[str, list] = {}
        self._unreadable: str | None = None
        self._tick = 0

    # ------------------------------------------------------------------ #
    # building a board                                                     #
    # ------------------------------------------------------------------ #

    def _now(self) -> str:
        self._tick += 1
        return (_START + timedelta(minutes=self._tick)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")

    def add(self, identifier: str, *, lane: str = "Queued",
            labels=("repo:bureau-pipeline", "agent:engineer"),
            description: str = "The card as its author wrote it.\n",
            relations=(), comments=(), children=(), title: str | None = None,
            parent: str | None = None) -> Card:
        """A card, with its comments oldest first. A comment is a body (the
        pipeline's) or `(body, user)`. `relations` names the cards that
        block it; a blocker not yet on the board is added in `Done`."""
        card = Card(identifier, title or f"{identifier} — a card", lane,
                    list(labels), description, relations=list(relations),
                    children=list(children), parent=parent)
        self.cards[identifier] = card
        for blocker in relations:
            if blocker not in self.cards:
                self.add(blocker, lane="Done")
        for entry in comments:
            body, user = entry if isinstance(entry, tuple) else (entry, FLEET_USER)
            self.say(identifier, body, user=user)
        return card

    def alert(self, kind: str, *, raised_at: str, severity: str = "WARN",
              title: str | None = None, card: str | None = "rule") -> dict:
        """One open alert the read serves. `card` defaults to the contract's
        rule over `kind`: its last `:` segment when that is `DRE-<n>`."""
        if card == "rule":
            last = kind.rsplit(":", 1)[-1]
            card = last if _CARD.match(last) else None
        row = {"kind": kind, "severity": severity,
               "title": title or f"the alert {kind}", "raised_at": raised_at,
               "card": card}
        self.alerts.append(row)
        return row

    def say(self, identifier: str, body: str, *, user: str = FLEET_USER) -> None:
        """A comment posted outside any code under test."""
        self.cards[identifier].comments.append(Comment(body, self._now(), user))

    def move(self, identifier: str, lane: str) -> None:
        """A person moves the card, outside any code under test."""
        self.cards[identifier].lane = lane

    def fail(self, kind: str, match: str = "") -> None:
        """The next write of `kind` whose detail contains `match` raises."""
        self._failures.setdefault(kind, []).append(match)

    def unreadable(self, reason: str) -> None:
        """The open-alerts read raises `ReadUnknown` naming `reason`."""
        self._unreadable = reason

    # ------------------------------------------------------------------ #
    # what a test reads back                                               #
    # ------------------------------------------------------------------ #

    def lane(self, identifier: str) -> str:
        return self.cards[identifier].lane

    def labels(self, identifier: str) -> list[str]:
        return list(self.cards[identifier].labels)

    def description(self, identifier: str) -> str:
        return self.cards[identifier].description

    def relations(self, identifier: str) -> list[str]:
        return list(self.cards[identifier].relations)

    def bodies(self, identifier: str) -> list[str]:
        return [c.body for c in self.cards[identifier].comments]

    def newest(self, identifier: str) -> str:
        return self.bodies(identifier)[-1]

    def writes(self, identifier: str | None = None) -> list[tuple[str, str]]:
        return [(kind, detail) for kind, card, detail in self.events
                if identifier is None or card == identifier]

    # ------------------------------------------------------------------ #
    # the answers                                                          #
    # ------------------------------------------------------------------ #

    def _check(self, kind: str, detail: str) -> None:
        pending = self._failures.get(kind) or []
        for i, match in enumerate(pending):
            if match in detail:
                pending.pop(i)
                raise BoardWriteFailed(f"the board refused {kind} ({detail[:60]})")

    def _card(self, identifier: str) -> Card:
        try:
            return self.cards[identifier]
        except KeyError:
            raise linear_ops.LinearError(f"issue {identifier} not found") from None

    def _by_id(self, linear_id: str) -> Card:
        return self._card(str(linear_id).removeprefix("id-"))

    def open_alerts(self) -> dict:
        self.reads.append(("open_alerts", ""))
        if self._unreadable is not None:
            raise bureau_read.ReadUnknown(self._unreadable)
        return {"schema": bureau_read.SCHEMA, "read_at": iso(datetime.now(UTC)),
                "alerts": sorted(copy.deepcopy(self.alerts),
                                 key=lambda r: (r["raised_at"], r["kind"]))}

    def comment_records(self, identifier, *, whole_thread=False):
        self.reads.append(("comment_records", identifier))
        comments = self._card(identifier).comments
        return [{"body": c.body, "authored_by_pipeline": c.user == FLEET_USER,
                 "created_at": c.created_at}
                for c in (comments if whole_thread else comments[-linear_ops.COMMENT_WINDOW:])]

    def get_issue(self, identifier, *, fresh=False):
        card = self._card(identifier)
        return {
            "id": f"id-{identifier}", "identifier": identifier,
            "title": card.title, "team": {"id": "team-dre"},
            "state": {"id": f"state-{card.lane}", "name": card.lane,
                      "type": "unstarted"},
            "labels": {"nodes": [{"name": n} for n in card.labels]},
            "children": {"nodes": [{"id": c} for c in card.children[:1]]},
            "project": None,
        }

    def _node(self, card: Card) -> dict:
        def blocker(ident: str) -> dict:
            other = self.cards.get(ident)
            return {"type": "blocks",
                    "issue": {"identifier": ident,
                              "state": {"name": other.lane if other else "unknown"}}}
        return {
            "id": f"id-{card.identifier}", "identifier": card.identifier,
            "title": card.title, "description": card.description,
            "state": {"name": card.lane},
            "labels": {"nodes": [{"name": n} for n in card.labels]},
            "parent": {"identifier": card.parent} if card.parent else None,
            "children": {"nodes": [{"id": f"id-{c}"} for c in card.children]},
            "inverseRelations": {"pageInfo": {"hasNextPage": card.relations_unread},
                                 "nodes": [blocker(i) for i in card.relations]},
        }

    def gql(self, query, variables=None, **kw):
        """The one-card read `alarm_repair` makes, and the relation mutation
        `linear_ops._add_blocked_by` makes. Any other mutation is a write
        nothing in the repair may make."""
        variables = variables or {}
        if query.lstrip().startswith("mutation"):
            if "issueRelationCreate" not in query:
                raise AssertionError(f"an unexpected mutation reached the board: {query[:80]}")
            data = variables.get("input") or {}
            blocker = self._by_id(data.get("issueId"))
            card = self._by_id(data.get("relatedIssueId"))
            if data.get("type") != "blocks":
                raise AssertionError(f"a relation of type {data.get('type')!r}")
            self._check("relation", blocker.identifier)
            if blocker.identifier not in card.relations:
                card.relations.append(blocker.identifier)
            self.events.append(("relation", card.identifier, blocker.identifier))
            return {"issueRelationCreate": {"success": True}}
        identifier = variables.get("id")
        self.reads.append(("gql", identifier))
        return {"issue": copy.deepcopy(self._node(self._card(identifier)))}

    # --- the writes -------------------------------------------------------

    def cmd_comment(self, identifier, body, *flags):
        self._check("comment", body)
        card = self._card(identifier)
        card.comments.append(Comment(body, self._now(), FLEET_USER))
        self.events.append(("comment", identifier, body))
        return None

    def cmd_state(self, identifier, state_name, *flags, **kw):
        raise AssertionError(f"the repair moved {identifier} to {state_name}: "
                             "its writes never include the lane")

    def set_description(self, identifier, body):
        raise AssertionError(f"the repair set {identifier}'s description: "
                             "it never writes the description")

    def add_label(self, identifier, label_name):
        self._check("add-label", label_name)
        card = self._card(identifier)
        if label_name not in card.labels:
            card.labels.append(label_name)
        self.events.append(("add-label", identifier, label_name))

    def remove_label(self, identifier, label_name):
        self._check("remove-label", label_name)
        card = self._card(identifier)
        card.labels = [n for n in card.labels if n != label_name]
        self.events.append(("remove-label", identifier, label_name))

    @contextlib.contextmanager
    def patched(self):
        """Every `linear_ops` call the repair reaches, and the open-alerts
        read, answered from here."""
        answers = {
            "comment_records": self.comment_records,
            "get_issue": self.get_issue,
            "gql": self.gql,
            "cmd_comment": self.cmd_comment,
            "cmd_state": self.cmd_state,
            "set_description": self.set_description,
            "add_label": self.add_label,
            "remove_label": self.remove_label,
        }
        with contextlib.ExitStack() as stack:
            for name, fn in answers.items():
                stack.enter_context(patch.object(linear_ops, name, side_effect=fn))
            stack.enter_context(patch.object(bureau_read, "open_alerts",
                                             side_effect=self.open_alerts))
            yield self
