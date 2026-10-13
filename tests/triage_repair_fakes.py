"""The test board for the Triage repair (DRE-7178, epic DRE-7174).

`TriageRepairBoard` holds each card's lane, labels, description and comments,
and answers the `linear_ops` calls that `triage_repair`, `hold`,
`answer_into_card` and `planning_escalation` make FROM that state. A write
changes the state, and the next read sees it, so a scenario test asserts the
lane a card ends in and the comments it carries — not only which functions
were called. A scenario runs the real `planning_escalation.escalate` over it,
with the real `config/lane-contract.json`; nothing here stubs it.

    board = TriageRepairBoard()
    board.add("DRE-7066", comments=[stamp, park_note])
    with board.patched():
        triage_repair.main(["select", "--out", str(out)])

`board.events` is every write, in the order it was made, as `(kind, card,
detail)`. `board.reads` is every comment read, with whether it paged the
whole thread. `board.fail(kind, …)` makes the next write of that kind raise,
the way a Linear error does.
"""
from __future__ import annotations

import contextlib
import copy
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import answer_into_card  # noqa: E402
import linear_ops  # noqa: E402

#: The fleet user: every comment the pipeline writes is this user's, and
#: `comment_records` says `authored_by_pipeline` for exactly these.
FLEET_USER = "user-agent-bureau"
#: Anybody else with comment access.
PERSON_USER = "user-a-person"

#: The window the board read and the unpaged readers return — the newest
#: `linear_ops.COMMENT_WINDOW` comments.
WINDOW = linear_ops.COMMENT_WINDOW

_START = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)


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
    comments: list[Comment] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    history: list[tuple[str, str]] = field(default_factory=list)


class TriageRepairBoard:
    def __init__(self) -> None:
        self.cards: dict[str, Card] = {}
        self.events: list[tuple[str, str, str]] = []
        self.reads: list[tuple[str, str, bool]] = []
        self._failures: dict[str, list] = {}
        self._tick = 0

    # ------------------------------------------------------------------ #
    # building a board                                                     #
    # ------------------------------------------------------------------ #

    def _now(self) -> str:
        self._tick += 1
        return (_START + timedelta(minutes=self._tick)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")

    def add(self, identifier: str, *, lane: str = "Triage",
            labels=("needs-human", "repo:agent-bureau", "agent:engineer"),
            description: str = "The card as it stood.\n",
            comments=(), children=(), title: str | None = None) -> Card:
        """A card, with its comments oldest first. A comment is a body (the
        pipeline's) or `(body, user)`."""
        card = Card(identifier, title or f"{identifier} — a card", lane,
                    list(labels), description, children=list(children),
                    history=[(lane, self._now())])
        self.cards[identifier] = card
        for entry in comments:
            body, user = entry if isinstance(entry, tuple) else (entry, FLEET_USER)
            self.say(identifier, body, user=user)
        return card

    def say(self, identifier: str, body: str, *, user: str = FLEET_USER) -> None:
        """A comment posted outside any code under test."""
        self.cards[identifier].comments.append(Comment(body, self._now(), user))

    def move(self, identifier: str, lane: str) -> None:
        """A person moves the card, outside any code under test."""
        card = self.cards[identifier]
        card.lane = lane
        card.history.append((lane, self._now()))

    def unlabel(self, identifier: str, label_name: str) -> None:
        """A person removes a label, outside any code under test."""
        card = self.cards[identifier]
        card.labels = [n for n in card.labels if n != label_name]

    def fail(self, kind: str, match: str = "") -> None:
        """The next write of `kind` whose detail contains `match` raises."""
        self._failures.setdefault(kind, []).append(match)

    # ------------------------------------------------------------------ #
    # what a test reads back                                               #
    # ------------------------------------------------------------------ #

    def lane(self, identifier: str) -> str:
        return self.cards[identifier].lane

    def labels(self, identifier: str) -> list[str]:
        return list(self.cards[identifier].labels)

    def description(self, identifier: str) -> str:
        return self.cards[identifier].description

    def bodies(self, identifier: str) -> list[str]:
        return [c.body for c in self.cards[identifier].comments]

    def newest(self, identifier: str) -> str:
        return self.bodies(identifier)[-1]

    def writes(self, identifier: str | None = None) -> list[tuple[str, str]]:
        return [(kind, detail) for kind, card, detail in self.events
                if identifier is None or card == identifier]

    # ------------------------------------------------------------------ #
    # the linear_ops answers                                               #
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

    def _thread(self, identifier: str, whole: bool, reader: str) -> list[Comment]:
        self.reads.append((reader, identifier, whole))
        comments = list(self._card(identifier).comments)
        return comments if whole else comments[-WINDOW:]

    def comment_records(self, identifier, *, whole_thread=False):
        return [{"body": c.body, "authored_by_pipeline": c.user == FLEET_USER,
                 "created_at": c.created_at}
                for c in self._thread(identifier, whole_thread, "comment_records")]

    def comment_timeline(self, identifier, *, whole_thread=False):
        return [{"body": c.body, "createdAt": c.created_at}
                for c in self._thread(identifier, whole_thread, "comment_timeline")]

    def comment_bodies(self, identifier):
        return [c.body for c in self._thread(identifier, False, "comment_bodies")]

    def count_comments(self, identifier, needle, *, since=None):
        return sum(1 for c in self._thread(identifier, True, "count_comments")
                   if needle in c.body)

    def thread_and_viewer(self, identifier, *needs, whole=False):
        nodes = [{"body": c.body, "createdAt": c.created_at, "user": {"id": c.user}}
                 for c in self._thread(identifier, whole, "thread_and_viewer")]
        return nodes, FLEET_USER

    def lane_history(self, identifier, first=50):
        return [{"createdAt": at, "toState": {"name": lane}}
                for lane, at in reversed(self._card(identifier).history)][:first]

    def get_issue(self, identifier, *, fresh=False):
        card = self._card(identifier)
        return {
            "id": f"id-{identifier}", "identifier": identifier,
            "title": card.title, "team": {"id": "team-dre"},
            "state": {"id": f"state-{card.lane}", "name": card.lane,
                      "type": "unstarted"},
            "labels": {"nodes": [{"name": n} for n in card.labels]},
            "children": {"nodes": [{"id": c} for c in card.children[:1]]},
        }

    def _board_node(self, card: Card) -> dict:
        window = card.comments[-WINDOW:]
        return {
            "id": f"id-{card.identifier}", "identifier": card.identifier,
            "title": card.title, "description": card.description,
            "createdAt": card.history[0][1], "updatedAt": card.history[-1][1],
            "state": {"name": card.lane},
            "labels": {"nodes": [{"name": n} for n in card.labels]},
            "parent": None,
            "children": {"nodes": [{"id": f"id-{c}", "identifier": c,
                                    "createdAt": card.history[0][1],
                                    "state": {"name": "Backlog"}}
                                   for c in card.children]},
            # Linear answers newest first; `window_nodes` turns it round.
            "comments": {
                "pageInfo": {"hasNextPage": len(card.comments) > WINDOW,
                             "endCursor": None},
                "nodes": [{"body": c.body, "createdAt": c.created_at,
                           "user": {"id": c.user}} for c in reversed(window)],
            },
            "relations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            "inverseRelations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            "history": {"nodes": [{"createdAt": at, "toState": {"name": lane}}
                                  for lane, at in card.history]},
        }

    def gql_paged(self, query, variables=None, **kw):
        states = set((variables or {}).get("states") or ())
        self.reads.append(("board", ",".join(sorted(states)), False))
        return [copy.deepcopy(self._board_node(c)) for c in self.cards.values()
                if c.lane in states]

    def gql(self, query, variables=None, **kw):
        """The single-issue reads `answer_into_card` and `hold` make."""
        identifier = (variables or {}).get("id")
        card = self._card(identifier)
        if query.lstrip().startswith("mutation"):
            raise AssertionError(f"an unexpected mutation reached the board: {query[:80]}")
        issue: dict = {"description": card.description}
        if "labels" in query:
            node = self._board_node(card)
            issue["labels"] = node["labels"]
            issue["comments"] = node["comments"]
        return {"issue": issue}

    # --- the writes -------------------------------------------------------

    def cmd_comment(self, identifier, body, *flags):
        self._check("comment", body)
        card = self._card(identifier)
        card.comments.append(Comment(body, self._now(), FLEET_USER))
        self.events.append(("comment", identifier, body))
        return None

    def cmd_state(self, identifier, state_name, *flags, expect=None, **kw):
        """The conditional write `linear_ops.cmd_state` makes with `expect`:
        refused, False, when the card's lane is not one the caller read."""
        self._check("state", state_name)
        card = self._card(identifier)
        if expect is not None and card.lane not in tuple(expect):
            self.events.append(("state-refused", identifier, state_name))
            return False
        card.lane = state_name
        card.history.append((state_name, self._now()))
        self.events.append(("state", identifier, state_name))
        return True

    def set_description(self, identifier, body):
        self._check("description", body)
        self._card(identifier).description = body
        self.events.append(("description", identifier, body))

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
        """Every `linear_ops` call the repair reaches, answered from here, and
        `answer_into_card.write` recorded as the write it is — the real one
        still runs, over this board."""
        real_write = answer_into_card.write

        def decisions_block(card, **kw):
            self._check("decisions-block", card)
            self.events.append(("decisions-block", card, ""))
            return real_write(card, **kw)

        answers = {
            "comment_records": self.comment_records,
            "comment_timeline": self.comment_timeline,
            "comment_bodies": self.comment_bodies,
            "count_comments": self.count_comments,
            "_thread_and_viewer": self.thread_and_viewer,
            "lane_history": self.lane_history,
            "get_issue": self.get_issue,
            "gql_paged": self.gql_paged,
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
            stack.enter_context(patch.object(answer_into_card, "write",
                                             side_effect=decisions_block))
            yield self
