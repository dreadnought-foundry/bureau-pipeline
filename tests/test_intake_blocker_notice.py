"""RED-first: an Intake card that blocks work already in flow is named to the
CEO in plain English, once — and the sweep moves nothing (DRE-4152).

THE RULE IS POINT 4 OF THE CEO'S SIGNED CONSOLE ANSWER, 2026-09-17 09:57 PT,
recorded on DRE-4141. A card waiting in Intake can be the `blockedBy` of a card
that is already being worked, and nothing said so: the blocked card just sat,
and the blocker looked like any other Intake card. Measured 2026-09-17, six
Intake cards blocked work in flow, none of them a child of a running epic.

Whether the blocker jumps the queue is a judgement about that card, and it
stays with a person. So the sweep SAYS it — one comment where the CEO already
reads, on the blocked card's epic or on the blocked card when it has none — and
moves nothing, labels nothing and files nothing.

WHAT THESE TESTS PIN, one block per acceptance criterion:

  1. an Intake card blocking an In Progress card produces exactly one comment,
     on the right card, naming both — and the Intake card is still in Intake
     with its labels unchanged;
  2. a second and third sweep post nothing for the same pair; a NEW blocked
     card against the same blocker gets its own single comment;
  3. a blocked card in Intake, Planning, or Backlog with no running epic
     produces nothing;
  4. a description that merely says "blocked by", with no relation, produces
     nothing;
  5. the comment passes the repo's plain-English check for CEO-facing text;
  6. the run log lists the pairs every pass;
  7. no code path here moves a card or writes a label.

Run: cd bureau-pipeline && python3 -m pytest tests/test_intake_blocker_notice.py -v
"""
from __future__ import annotations

import ast
import contextlib
import copy
import inspect
import os
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import planning_escalation  # noqa: E402 — the repo's plain-English check
import reconcile  # noqa: E402
from test_intake_no_age_out import _main_mocks  # noqa: E402

#: The window a card's default comment read is served from (the newest fifty).
WINDOW = 50


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _mine() -> str:
    return f"repo:{reconcile.REPO_SLUG}"


def card(identifier, lane, *, title=None, parent=None, labels=None,
         description="work"):
    """A card on the board. `parent` is the identifier of its epic."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title or f"the work of {identifier}",
        "description": description,
        "updatedAt": _now(),
        "createdAt": _now(),
        "priority": 3,
        "state": {"name": lane},
        "labels": {"nodes": [{"name": n} for n in (labels if labels is not None
                                                   else [_mine()])]},
        "children": {"nodes": []},
        "parent": parent,
        "comments": [],
    }


def epic(identifier, lane="In Progress", **kw):
    c = card(identifier, lane, title=f"[EPIC] the plan {identifier}", **kw)
    c["children"] = {"nodes": [{"id": "kid"}]}
    return c


class Board:
    """One Linear team. `relations` are (blocker, blocked, type) triples —
    the board's own `blockedBy` relations, never prose. Writes land here so a
    second sweep reads what the first one did."""

    def __init__(self, cards, relations=()):
        self.cards = {c["identifier"]: c for c in cards}
        self.relations = list(relations)
        self.posted: list[tuple[str, str]] = []
        self.queries: list[tuple[str, dict]] = []

    def add(self, c):
        self.cards[c["identifier"]] = c

    def relate(self, blocker, blocked, kind="blocks"):
        self.relations.append((blocker, blocked, kind))

    # --- reads ---------------------------------------------------------
    def _row(self, c):
        row = {k: v for k, v in c.items() if k != "parent"}
        row["comments"] = {"pageInfo": {"hasNextPage": False},
                           "nodes": list(reversed(c["comments"][-WINDOW:]))}
        return copy.deepcopy(row)

    def active_cards(self, states=reconcile.SWEEP_STATES):
        return [self._row(c) for c in self.cards.values()
                if c["state"]["name"] in states]

    def _brief(self, ident):
        c = self.cards[ident]
        return {"identifier": ident, "title": c["title"],
                "state": {"name": c["state"]["name"]}}

    def gql_paged(self, query, variables=None, *, connection="issues"):
        """The blocked-side read answers with EVERY card that is not in
        Intake — the server's filter is not modelled, so what the sweep
        decides it decides on its own reading, never on the fake's."""
        self.queries.append((query, copy.deepcopy(variables or {})))
        if "inverseRelations" not in query or "parent" not in query:
            return []
        out = []
        for c in self.cards.values():
            if c["state"]["name"] == "Intake":
                continue
            parent = c.get("parent")
            out.append({
                "id": c["id"], "identifier": c["identifier"],
                "title": c["title"], "description": c["description"],
                "state": {"name": c["state"]["name"]},
                "labels": copy.deepcopy(c["labels"]),
                "parent": self._brief(parent) if parent else None,
                "inverseRelations": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"type": kind, "issue": self._brief(blocker)}
                        for blocker, blocked, kind in self.relations
                        if blocked == c["identifier"]
                    ],
                },
            })
        return out

    def comment_bodies(self, identifier, *, whole_thread=False):
        nodes = self.cards[identifier]["comments"]
        if not whole_thread:
            nodes = nodes[-WINDOW:]
        return [n["body"] for n in nodes]

    def comment_records(self, identifier, *, whole_thread=False):
        return [{"body": b, "authored_by_pipeline": True, "created_at": _now()}
                for b in self.comment_bodies(identifier, whole_thread=whole_thread)]

    def get_issue(self, identifier):
        return copy.deepcopy(self.cards[identifier])

    # --- writes --------------------------------------------------------
    def cmd_comment(self, identifier, body, *flags):
        self.cards[identifier]["comments"].append(
            {"body": body, "createdAt": _now(), "user": {"id": "fleet"}})
        self.posted.append((identifier, body))

    # --- what a test asserts on ----------------------------------------
    def notices(self, identifier=None):
        return [(i, b) for i, b in self.posted
                if f"{reconcile.INTAKE_BLOCKER_OPENER}:" in b
                and (identifier is None or i == identifier)]


@contextlib.contextmanager
def wired(board):
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(
            reconcile, "active_cards", side_effect=board.active_cards))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "gql_paged", side_effect=board.gql_paged))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "comment_bodies", side_effect=board.comment_bodies))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "comment_records", side_effect=board.comment_records))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=board.cmd_comment))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "get_issue", side_effect=board.get_issue))
        writes = SimpleWrites(
            advance=stack.enter_context(mock.patch.object(reconcile.linear_ops, "cmd_advance")),
            state=stack.enter_context(mock.patch.object(reconcile.linear_ops, "cmd_state")),
            add_label=stack.enter_context(mock.patch.object(reconcile.linear_ops, "add_label")),
            remove_label=stack.enter_context(
                mock.patch.object(reconcile.linear_ops, "remove_label")),
        )
        yield writes


class SimpleWrites:
    def __init__(self, **mocks):
        self.__dict__.update(mocks)

    def none(self) -> bool:
        return not any(m.called for m in self.__dict__.values())


def sweep(board):
    """The notice's own phase, over `board`."""
    with wired(board) as writes:
        reconcile.report_intake_blockers()
    return writes


def full_sweep(board):
    """A whole `main()` pass over `board` — nothing stubs this phase."""
    reconcile._stale_defects.clear()
    with wired(board) as writes, contextlib.ExitStack() as stack:
        for m in _main_mocks():
            if getattr(m, "attribute", None) == "report_intake_blockers":
                continue  # this suite is about the phase _main_mocks silences
            stack.enter_context(m)
        stack.enter_context(mock.patch.object(reconcile, "pr_for", return_value=None))
        stack.enter_context(mock.patch.object(reconcile, "agent_run_alive", return_value=True))
        reconcile.main()
    return writes


def the_board(*, blocked_lane="In Progress", with_epic=True):
    """DRE-100 waits in Intake and blocks DRE-200, which is in `blocked_lane`
    under the In Progress epic DRE-900 (or under no epic)."""
    cards = [card("DRE-100", "Intake", title="the access request",
                  labels=["Feature"])]
    if with_epic:
        cards.append(epic("DRE-900"))
    cards.append(card("DRE-200", blocked_lane, title="the switch-over",
                      parent="DRE-900" if with_epic else None))
    return Board(cards, [("DRE-100", "DRE-200", "blocks")])


# --------------------------------------------------------------------------
# 1: THE CRITERION — one comment, on the right card, naming both
# --------------------------------------------------------------------------
def test_an_intake_card_blocking_in_progress_work_is_named_once_on_the_epic():
    board = the_board()
    before = copy.deepcopy(board.cards["DRE-100"])
    writes = full_sweep(board)
    notices = board.notices()
    assert len(notices) == 1, board.posted
    where, body = notices[0]
    assert where == "DRE-900", "the notice goes on the blocked card's epic"
    assert "DRE-100" in body and "DRE-200" in body, body
    # moved nothing, labelled nothing
    assert board.cards["DRE-100"]["state"]["name"] == "Intake"
    assert board.cards["DRE-100"]["labels"] == before["labels"]
    assert writes.none(), "the sweep moved a card or wrote a label"
    assert not reconcile._write_failures and not reconcile._read_failures


def test_a_blocked_card_with_no_epic_carries_the_notice_itself():
    board = the_board(with_epic=False)
    writes = sweep(board)
    assert [i for i, _ in board.notices()] == ["DRE-200"]
    assert writes.none()


@pytest.mark.parametrize("lane", ["Todo", "In Progress", "In Review"])
def test_every_work_lane_counts_as_in_flow(lane):
    board = the_board(blocked_lane=lane)
    sweep(board)
    assert [i for i, _ in board.notices()] == ["DRE-900"], lane


def test_the_notice_names_the_two_things_a_person_can_do():
    board = the_board()
    sweep(board)
    body = board.notices()[0][1]
    assert "Urgent" in body
    assert "groom batch" in body
    assert "the access request" in body, "the Intake card is named by its title too"
    assert "the switch-over" in body


# --------------------------------------------------------------------------
# 2: once per pair, and a new pair gets its own
# --------------------------------------------------------------------------
def test_a_second_and_third_sweep_post_nothing_for_the_same_pair():
    board = the_board()
    for _ in range(3):
        sweep(board)
    assert len(board.notices()) == 1, board.posted


def test_a_new_blocked_card_against_the_same_blocker_gets_its_own_notice():
    board = the_board()
    sweep(board)
    board.add(card("DRE-201", "Todo", parent="DRE-900"))
    board.relate("DRE-100", "DRE-201")
    sweep(board)
    sweep(board)
    notices = board.notices("DRE-900")
    assert len(notices) == 2, board.posted
    assert "DRE-201" in notices[1][1]


def test_a_pair_is_not_repeated_once_its_notice_is_past_the_comment_window():
    """'Never repeated' holds on a busy epic: the notice is read for on the
    whole thread before a second one is posted, not only the newest fifty."""
    board = the_board()
    sweep(board)
    for n in range(WINDOW + 10):
        board.cards["DRE-900"]["comments"].append(
            {"body": f"⏳ progress {n}", "createdAt": _now()})
    sweep(board)
    assert len(board.notices()) == 1, "the pair was named a second time"


def test_one_card_numbered_as_a_prefix_of_another_is_its_own_pair():
    """`DRE-20` holding up `DRE-200` is not the pair `DRE-20` → `DRE-2001`."""
    board = Board([
        card("DRE-20", "Intake"),
        card("DRE-200", "In Progress"),
        card("DRE-2001", "In Progress"),
    ], [("DRE-20", "DRE-200", "blocks")])
    sweep(board)
    board.relate("DRE-20", "DRE-2001")
    sweep(board)
    assert sorted(i for i, _ in board.notices()) == ["DRE-200", "DRE-2001"]


# --------------------------------------------------------------------------
# 3: only work in flow counts
# --------------------------------------------------------------------------
@pytest.mark.parametrize("lane", ["Intake", "Planning", "Green Light", "Done"])
def test_a_blocked_card_not_in_flow_produces_nothing(lane):
    board = the_board(blocked_lane=lane)
    writes = sweep(board)
    assert board.notices() == []
    assert writes.none()


def test_a_backlog_card_with_no_running_epic_produces_nothing():
    for parent_lane in ("Planning", "Green Light", "Backlog"):
        board = Board([
            card("DRE-100", "Intake"),
            epic("DRE-900", parent_lane),
            card("DRE-200", "Backlog", parent="DRE-900"),
        ], [("DRE-100", "DRE-200", "blocks")])
        sweep(board)
        assert board.notices() == [], parent_lane
    orphan = Board([card("DRE-100", "Intake"), card("DRE-200", "Backlog")],
                   [("DRE-100", "DRE-200", "blocks")])
    sweep(orphan)
    assert orphan.notices() == []


def test_a_backlog_child_of_an_in_progress_epic_is_in_flow():
    board = the_board(blocked_lane="Backlog")
    sweep(board)
    assert [i for i, _ in board.notices()] == ["DRE-900"]


def test_a_blocker_that_has_left_intake_produces_nothing():
    board = the_board()
    board.cards["DRE-100"]["state"]["name"] = "Planning"
    sweep(board)
    assert board.notices() == []


def test_a_relation_that_is_not_blocks_produces_nothing():
    board = the_board()
    board.relations = [("DRE-100", "DRE-200", "related")]
    sweep(board)
    assert board.notices() == []


def test_another_repos_blocked_card_is_left_to_that_repos_sweep():
    board = the_board(with_epic=False)
    board.cards["DRE-200"]["labels"] = {"nodes": [{"name": "repo:someone-else"}]}
    sweep(board)
    assert board.notices() == []


# --------------------------------------------------------------------------
# 4: prose is not a relation
# --------------------------------------------------------------------------
def test_a_description_that_says_blocked_by_with_no_relation_produces_nothing():
    board = Board([
        card("DRE-100", "Intake"),
        card("DRE-200", "In Progress",
             description="**Blocked by:** DRE-100\n\nThis is blocked by DRE-100."),
    ])
    writes = sweep(board)
    assert board.notices() == []
    assert writes.none()


# --------------------------------------------------------------------------
# 5: plain English for the CEO
# --------------------------------------------------------------------------
def test_the_notice_passes_the_plain_english_check():
    board = the_board()
    sweep(board)
    body = board.notices()[0][1]
    assert planning_escalation.refusal(body) is None, planning_escalation.jargon(body)
    assert ";" not in body, "em-dashes, never semicolons (standards/comms.md)"


def test_a_title_written_in_code_is_left_out_rather_than_put_in_front_of_the_ceo():
    board = the_board()
    board.cards["DRE-100"]["title"] = "fix scripts/reconcile.py so gh pr list works"
    sweep(board)
    body = board.notices()[0][1]
    assert planning_escalation.refusal(body) is None, planning_escalation.jargon(body)
    assert "DRE-100" in body and "DRE-200" in body


# --------------------------------------------------------------------------
# 6: the run log lists the pairs every pass
# --------------------------------------------------------------------------
def _pair_lines(out: str) -> list[str]:
    return [line for line in out.splitlines()
            if line.startswith(f"{reconcile.INTAKE_BLOCKER_OPENER}:")
            and "DRE-100" in line and "DRE-200" in line]


def test_the_run_log_lists_the_pair_on_every_pass(capsys):
    board = the_board()
    for _ in range(3):
        sweep(board)
        assert len(_pair_lines(capsys.readouterr().out)) == 1


def test_the_run_log_says_so_when_there_is_no_pair(capsys):
    board = Board([card("DRE-100", "Intake"), card("DRE-200", "In Progress")])
    sweep(board)
    lines = [line for line in capsys.readouterr().out.splitlines()
             if line.startswith(f"{reconcile.INTAKE_BLOCKER_OPENER}:")]
    assert len(lines) == 1 and "0 " in lines[0], lines


def test_a_full_sweep_prints_the_pair(capsys):
    full_sweep(the_board())
    assert len(_pair_lines(capsys.readouterr().out)) == 1


# --------------------------------------------------------------------------
# 7: nothing here moves a card or writes a label
# --------------------------------------------------------------------------
_WRITES = {"cmd_state", "cmd_advance", "add_label", "remove_label",
           "cmd_create", "cmd_oneoff", "cmd_subissue", "cmd_unpark",
           "guarded_state_write"}


def _calls_in(fn) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return names


def test_no_code_path_in_the_phase_moves_a_card_or_writes_a_label():
    for fn in (reconcile.report_intake_blockers, reconcile.intake_blocker_notice,
               reconcile.intake_blocker_pairs):
        assert not (_calls_in(fn) & _WRITES), fn.__name__


def test_nothing_is_filed_in_triage_or_green_light():
    board = the_board()
    sweep(board)
    assert set(board.cards) == {"DRE-100", "DRE-200", "DRE-900"}
    for c in board.cards.values():
        assert c["state"]["name"] not in ("Triage", "Green Light")


# --------------------------------------------------------------------------
# cost and honesty
# --------------------------------------------------------------------------
def test_no_request_is_made_when_intake_is_empty():
    board = Board([card("DRE-200", "In Progress")])
    sweep(board)
    assert board.queries == []


def test_no_request_is_made_when_nothing_is_in_flow():
    board = Board([card("DRE-100", "Intake"), card("DRE-200", "Backlog")],
                  [("DRE-100", "DRE-200", "blocks")])
    sweep(board)
    assert board.queries == []


def test_the_read_asks_for_the_lanes_the_rule_names():
    board = the_board()
    sweep(board)
    (_query, variables), = board.queries
    assert set(variables["states"]) == {"Todo", "In Progress", "In Review"}
    assert variables["epicStates"] == ["In Progress"]


def test_an_unreadable_board_is_raised_never_read_as_no_pairs():
    board = the_board()
    with wired(board):
        with mock.patch.object(reconcile.linear_ops, "gql_paged",
                               side_effect=reconcile.linear_ops.LinearError("boom")):
            with pytest.raises(reconcile.linear_ops.LinearError):
                reconcile.report_intake_blockers()


def test_a_full_relation_page_is_read_to_its_end():
    board = the_board()
    real = board.gql_paged

    def full_page(query, variables=None, **kw):
        rows = real(query, variables, **kw)
        for row in rows:
            if row["identifier"] == "DRE-200":
                row["inverseRelations"]["pageInfo"]["hasNextPage"] = True
        return rows

    board.gql_paged = full_page
    with mock.patch.object(reconcile, "complete_inverse_relations") as complete:
        sweep(board)
    assert complete.called
