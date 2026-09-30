"""RED-first: a card raised to Urgent after the rule ships goes straight from
Intake to Planning (DRE-4150).

THE RULE IS POINT 2 OF THE CEO'S SIGNED CONSOLE ANSWER, 2026-09-17 09:57 PT,
recorded on DRE-4141. An Urgent card should not wait for the next groom batch
and its approval, so the sweep moves it to Planning on its next pass. It still
goes through Planning's classifier and critic like any card — urgency skips the
queue, not the checks.

WHY THE LIMITS EXIST. Measured 2026-09-17 05:55 PT: 24 of 286 Intake cards
already carried Urgent — six the CEO had excluded from the 2026-09-15 groom
batch with signed receipts, four epics, eleven children of epics still in
Intake. A fast path keyed on the label alone would have moved all 24 on its
first sweep. "Urgent" on this board has meant "important", so the rule reads
the ACT of raising a card to Urgent after a fixed date, never the label.

WHAT THESE TESTS PIN, one block per acceptance criterion:

  1. a non-epic Intake card raised to Urgent after the ship moment is in
     Planning after ONE full sweep with exactly one comment naming the rule,
     and a second sweep adds nothing;
  2. a card already Urgent before the ship moment is untouched — decided off
     the card's own Linear history, never its current label;
  3. a card the CEO excluded with a signed receipt is untouched even when it is
     raised after the ship moment;
  4. an Urgent epic is untouched; an Urgent child of an epic that is not In
     Progress is untouched; an Urgent child of an In Progress epic moves;
  5. five eligible cards: three move this sweep, two the next, and the run log
     names the two that waited;
  6. the move happens with `INTAKE_HOLD` set;
  7. two sweeps from two repos over the same board move three cards between
     them, not six.

Run: cd bureau-pipeline && python3 -m pytest tests/test_intake_urgent_fast_path.py -v
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import sys
from datetime import UTC, datetime, timedelta
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

import console_receipt  # noqa: E402
import groomer  # noqa: E402
import lane_contract  # noqa: E402
import reconcile  # noqa: E402
from test_intake_no_age_out import _main_mocks  # noqa: E402

#: The groomer's standing card — where the CEO's per-card exclusions live.
STANDING = "DRE-4541"

#: The config file the ship moment is read from. Read here as a FILE, not off
#: the module, so a constant that quietly stopped coming from config fails.
CONFIG = ROOT / "config" / "urgent-fast-path.json"


def _ship() -> datetime:
    raw = json.loads(CONFIG.read_text())["ships_at"]
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _tag() -> str:
    return reconcile.URGENT_FAST_PATH_OPENER


# --------------------------------------------------------------------------
# a board both sweeps share
# --------------------------------------------------------------------------
class Board:
    """One Linear team, as every repo's sweep sees it: the lanes, each card's
    comments and history, and the root comment search the cross-repo cap reads.

    Writes land here, so a second sweep — or a second repo's sweep — reads
    what the first one did. `age(minutes)` moves every recorded moment into
    the past, which is how a test says "the next sweep is fifteen minutes
    later" without touching the clock the code reads."""

    def __init__(self, cards, standing=()):
        self.cards = {c["identifier"]: c for c in cards}
        self.standing = list(standing)
        self.advances: list[tuple] = []
        self.posted: list[tuple[str, str]] = []

    # --- reads ---------------------------------------------------------
    def _board_row(self, card):
        """What the sweep's ONE board read carries — no history, no parent,
        no createdAt. Those come only from the fast path's own read."""
        row = {k: v for k, v in card.items()
               if k not in ("history", "parent", "createdAt")}
        row["comments"] = {
            "pageInfo": {"hasNextPage": False},
            "nodes": list(reversed(card["comments"])),  # newest first
        }
        return copy.deepcopy(row)

    def active_cards(self, states=reconcile.SWEEP_STATES):
        return [self._board_row(c) for c in self.cards.values()
                if c["state"]["name"] in states]

    def gql_paged(self, query, variables=None, *, connection="issues"):
        variables = variables or {}
        if connection == "comments":
            since = datetime.fromisoformat(variables["since"].replace("Z", "+00:00"))
            out = []
            for card in self.cards.values():
                for node in card["comments"]:
                    at = datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00"))
                    if at > since and f"{_tag()}:" in node["body"]:
                        out.append({"createdAt": node["createdAt"],
                                    "body": node["body"],
                                    "issue": {"identifier": card["identifier"]}})
            return out
        if "history" in query:
            wanted = set(variables.get("ids") or ())
            return [
                {
                    "id": c["id"], "identifier": c["identifier"],
                    "priority": c["priority"], "createdAt": c["createdAt"],
                    "parent": copy.deepcopy(c.get("parent")),
                    "history": {"nodes": list(reversed(c["history"]))},
                }
                for c in self.cards.values() if c["id"] in wanted
            ]
        return []

    def comment_records(self, identifier, *, whole_thread=False):
        if identifier == STANDING:
            return copy.deepcopy(self.standing)
        return [{"body": n["body"], "authored_by_pipeline": True,
                 "created_at": n["createdAt"]}
                for n in self.cards[identifier]["comments"]]

    def comment_bodies(self, identifier):
        return [n["body"] for n in self.cards[identifier]["comments"]]

    # --- writes --------------------------------------------------------
    def cmd_comment(self, identifier, body, *flags):
        self.cards[identifier]["comments"].append({
            "body": body, "createdAt": _iso(datetime.now(UTC)),
            "user": {"id": "fleet"},
        })
        self.posted.append((identifier, body))

    def cmd_advance(self, identifier, to_state, from_states_csv, *flags):
        self.advances.append((identifier, to_state, from_states_csv, flags))
        card = self.cards[identifier]
        allowed = [s.strip().lower() for s in from_states_csv.split(",")]
        if card["state"]["name"].lower() in allowed:
            card["state"]["name"] = to_state

    # --- the clock -----------------------------------------------------
    def age(self, minutes):
        """Every comment on the board is now `minutes` older."""
        for card in self.cards.values():
            for node in card["comments"]:
                at = datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00"))
                node["createdAt"] = _iso(at - timedelta(minutes=minutes))

    # --- what a test asserts on ----------------------------------------
    def lane(self, identifier):
        return self.cards[identifier]["state"]["name"]

    def rule_comments(self, identifier):
        return [n["body"] for n in self.cards[identifier]["comments"]
                if f"{_tag()}:" in n["body"]]

    def moved(self):
        return sorted(i for i, c in self.cards.items()
                      if c["state"]["name"] == "Planning")


def card(identifier, *, raised=None, created=None, priority=1, title=None,
         children=False, parent_state=None, lane="Intake", updated=None,
         history=None):
    """An Intake card. `raised` is when it was raised to Urgent (a history
    entry); with no `raised`, a card at Urgent was CREATED at it, and its
    `created` moment is the raise. Every moment defaults relative to the ship
    moment, never to now."""
    ship = _ship()
    created = created or (ship + timedelta(hours=1) if raised is None
                          else ship - timedelta(days=30))
    if history is None:
        history = []
        if raised is not None:
            history.append({"createdAt": _iso(raised), "fromPriority": 3,
                            "toPriority": priority})
    last = max([created] + [
        datetime.fromisoformat(h["createdAt"].replace("Z", "+00:00"))
        for h in history])
    number = identifier.split("-")[1]
    parent = None
    if parent_state:
        parent = {"identifier": f"DRE-9{number}", "state": {"name": parent_state}}
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title or f"urgent work {identifier}",
        "description": "work",
        "updatedAt": _iso(updated or last),
        "createdAt": _iso(created),
        "priority": priority,
        "state": {"name": lane},
        "labels": {"nodes": []},
        "children": {"nodes": [{"id": "kid"}] if children else []},
        "parent": parent,
        "history": history,
        "comments": [],
    }


@pytest.fixture(autouse=True)
def _standing_card(monkeypatch):
    """The groomer's standing card is configured, as it is on the fleet."""
    monkeypatch.setenv("GROOM_PROPOSAL_CARD", STANDING)
    monkeypatch.delenv("INTAKE_HOLD", raising=False)


@contextlib.contextmanager
def wired(board, *, snapshot=None):
    """Point the sweep at `board`. `snapshot` serves the LANE read from a
    different board — a sweep whose board read was taken before another
    repo's writes landed — while every other read and write stays live."""
    lanes = snapshot or board
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(
            reconcile, "active_cards", side_effect=lanes.active_cards))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "gql_paged", side_effect=board.gql_paged))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "comment_records",
            side_effect=board.comment_records))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "comment_bodies",
            side_effect=board.comment_bodies))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=board.cmd_comment))
        stack.enter_context(mock.patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=board.cmd_advance))
        yield board


def sweep(board, **kw):
    """The fast path's own phase, over `board`."""
    with wired(board, **kw):
        return reconcile.advance_urgent_intake()


@contextlib.contextmanager
def full_sweep(board):
    """A whole `main()` pass over `board` — nothing stubs the Intake phase."""
    reconcile._stale_defects.clear()
    with wired(board), contextlib.ExitStack() as stack:
        for m in _main_mocks():
            stack.enter_context(m)
        reconcile.main()
        yield board


def _fast_path_lines(out: str) -> list[str]:
    return [line for line in out.splitlines()
            if line.startswith(f"{_tag()}:")]


# --------------------------------------------------------------------------
# 0: the ship moment is a dated constant in config, never "now"
# --------------------------------------------------------------------------
def test_the_ship_moment_is_a_dated_constant_read_from_config():
    raw = json.loads(CONFIG.read_text())["ships_at"]
    assert raw.endswith("Z"), "a moment with no zone is not a moment"
    assert datetime.fromisoformat(raw.replace("Z", "+00:00")).tzinfo is not None
    assert reconcile.URGENT_SHIPS_AT == _ship()


def test_the_cap_is_three_per_sweep():
    assert reconcile.URGENT_SWEEP_CAP == 3
    assert json.loads(CONFIG.read_text())["per_sweep_cap"] == 3


# --------------------------------------------------------------------------
# 1: THE CRITERION — raised after the ship moment, in Planning after one sweep
# --------------------------------------------------------------------------
def test_an_urgent_card_raised_after_the_ship_moment_is_in_planning_after_one_sweep():
    board = Board([card("DRE-101", raised=_ship() + timedelta(hours=2))])
    with full_sweep(board):
        pass
    assert board.lane("DRE-101") == "Planning"
    comments = board.rule_comments("DRE-101")
    assert len(comments) == 1, comments
    assert "Urgent" in comments[0]
    assert "DRE-4150" in comments[0], "the comment names the rule it moved under"
    assert not reconcile._write_failures


def test_a_second_sweep_adds_nothing():
    board = Board([card("DRE-101", raised=_ship() + timedelta(hours=2))])
    with full_sweep(board):
        pass
    posted, advanced = len(board.posted), len(board.advances)
    board.age(20)
    with full_sweep(board):
        pass
    assert len(board.posted) == posted, board.posted
    assert len(board.advances) == advanced, board.advances
    assert len(board.rule_comments("DRE-101")) == 1


def test_a_card_carrying_the_receipt_is_never_moved_again():
    """The receipt is the idempotency key. A card that carries it and is back
    in Intake — a person put it there — is left alone: the rule takes a card
    out once."""
    board = Board([card("DRE-101", raised=_ship() + timedelta(hours=2))])
    sweep(board)
    board.cards["DRE-101"]["state"]["name"] = "Intake"
    board.age(60)
    sweep(board)
    assert board.lane("DRE-101") == "Intake"
    assert len(board.rule_comments("DRE-101")) == 1


def test_a_card_created_at_urgent_after_the_ship_moment_moves():
    """"Raised to priority 1 (or created at it)": Linear records no priority
    change on a card created at Urgent, so its creation is the raise."""
    board = Board([card("DRE-102", created=_ship() + timedelta(hours=3))])
    sweep(board)
    assert board.lane("DRE-102") == "Planning"


def test_the_move_is_out_of_intake_into_planning_and_refuses_an_epic():
    board = Board([card("DRE-103", raised=_ship() + timedelta(hours=2))])
    sweep(board)
    assert board.advances == [("DRE-103", "Planning", "Intake", ("--not-epic",))]


# --------------------------------------------------------------------------
# 2: already Urgent before the ship moment — decided off the HISTORY
# --------------------------------------------------------------------------
def test_a_card_raised_to_urgent_before_the_ship_moment_is_untouched():
    """Built from the card's history, not its label: this card is at Urgent
    NOW, and was touched after the ship moment (a comment, an edit), so only
    the history entry says it was already Urgent before the rule shipped."""
    ship = _ship()
    board = Board([card("DRE-201", raised=ship - timedelta(days=12),
                        updated=ship + timedelta(hours=5))])
    sweep(board)
    assert board.lane("DRE-201") == "Intake"
    assert board.posted == []


def test_a_card_created_at_urgent_before_the_ship_moment_is_untouched():
    ship = _ship()
    board = Board([card("DRE-202", created=ship - timedelta(days=40),
                        updated=ship + timedelta(hours=5))])
    sweep(board)
    assert board.lane("DRE-202") == "Intake"
    assert board.posted == []


def test_an_old_card_raised_after_the_ship_moment_moves():
    """The same card as above, the other way round — created long before the
    rule, raised to Urgent after it. The ACT of raising is what counts."""
    ship = _ship()
    board = Board([card("DRE-203", created=ship - timedelta(days=40),
                        raised=ship + timedelta(hours=1))])
    sweep(board)
    assert board.lane("DRE-203") == "Planning"


def test_the_newest_raise_decides_not_the_first():
    """Urgent before the ship moment, lowered, then raised again after it: the
    card was raised to Urgent after the rule shipped."""
    ship = _ship()
    history = [
        {"createdAt": _iso(ship - timedelta(days=10)), "fromPriority": 3, "toPriority": 1},
        {"createdAt": _iso(ship - timedelta(days=5)), "fromPriority": 1, "toPriority": 3},
        {"createdAt": _iso(ship + timedelta(hours=4)), "fromPriority": 3, "toPriority": 1},
    ]
    board = Board([card("DRE-204", created=ship - timedelta(days=40),
                        history=history)])
    sweep(board)
    assert board.lane("DRE-204") == "Planning"


def test_the_twenty_four_already_urgent_cards_stay_where_they_are():
    """The measured board of 2026-09-17: every Urgent card then in Intake was
    raised before the rule, and a label-keyed fast path would move all 24."""
    ship = _ship()
    cards = [card(f"DRE-{2300 + n}", raised=ship - timedelta(days=20 + n),
                  updated=ship + timedelta(hours=n + 1)) for n in range(24)]
    board = Board(cards)
    sweep(board)
    assert board.moved() == []
    assert board.posted == []


@pytest.mark.parametrize("priority", [0, 2, 3, 4])
def test_a_card_not_at_urgent_is_untouched_whenever_it_was_raised(priority):
    board = Board([card("DRE-205", raised=_ship() + timedelta(hours=1),
                        priority=priority)])
    sweep(board)
    assert board.lane("DRE-205") == "Intake"


# --------------------------------------------------------------------------
# 3: the CEO's signed exclusion
# --------------------------------------------------------------------------
@pytest.fixture
def console_key(monkeypatch):
    """The console's published key is the TEST key (the groomer tests' own
    fixture, applied here)."""
    import console_receipt_vectors as V

    openssl = V.capable_openssl()
    if openssl is None:
        if os.environ.get("CI"):
            pytest.fail("no Ed25519-capable openssl on a CI runner")
        pytest.skip("no Ed25519-capable openssl on this machine")
    monkeypatch.setenv("OPENSSL_BIN", openssl)
    monkeypatch.setattr(console_receipt, "_OPENSSL", None, raising=False)
    monkeypatch.setattr(groomer, "_VERIFIER", console_receipt.Verifier(
        key_loader=lambda: console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)))


def _signed_exclusion(identifier):
    from test_groomer_console_receipt import receipted

    pid = "abc123def456"
    marker = groomer.decision_comment(groomer.EXCLUDE_TAG, pid, card=identifier)
    return receipted(marker, card=STANDING, proposal=pid)


def test_a_card_the_ceo_excluded_is_untouched_even_when_raised_after_the_ship_moment(
        console_key):
    board = Board(
        [card("DRE-301", raised=_ship() + timedelta(hours=2)),
         card("DRE-302", raised=_ship() + timedelta(hours=3))],
        standing=[_signed_exclusion("DRE-301")],
    )
    sweep(board)
    assert board.lane("DRE-301") == "Intake", "the CEO excluded this card"
    assert board.rule_comments("DRE-301") == []
    assert board.lane("DRE-302") == "Planning", "an exclusion names one card"


def test_an_unsigned_pipeline_exclusion_decides_nothing(console_key):
    """Only a decider's marker counts — the groomer's own rule. The fleet
    writing `groom-excluded` for itself cannot keep a card off the fast path
    any more than it can keep one out of a batch."""
    marker = groomer.decision_comment(groomer.EXCLUDE_TAG, "abc123def456",
                                      card="DRE-303")
    board = Board([card("DRE-303", raised=_ship() + timedelta(hours=2))],
                  standing=[{"body": marker, "authored_by_pipeline": True,
                             "created_at": _iso(_ship())}])
    sweep(board)
    assert board.lane("DRE-303") == "Planning"


def test_no_standing_card_configured_moves_nothing(monkeypatch, capsys):
    """The exclusions live on the groomer's standing card. A sweep that cannot
    read them cannot tell an excluded card from an eligible one, so it moves
    nothing and says why — an unread exclusion is not an absent one."""
    monkeypatch.delenv("GROOM_PROPOSAL_CARD", raising=False)
    board = Board([card("DRE-304", raised=_ship() + timedelta(hours=2))])
    sweep(board)
    assert board.lane("DRE-304") == "Intake"
    assert board.posted == []
    lines = _fast_path_lines(capsys.readouterr().out)
    assert any("GROOM_PROPOSAL_CARD" in line for line in lines), lines


def test_an_exclusion_that_could_not_be_checked_moves_nothing(
        console_key, monkeypatch):
    """The console's key unreadable: a signed exclusion cannot be verified, and
    reading it as refused would move a card the CEO kept out (DRE-4153's
    rule — an unchecked receipt is not a refused one)."""
    def down():
        raise console_receipt.KeyUnavailable("console unreachable")

    exclusion = _signed_exclusion("DRE-305")
    monkeypatch.setattr(groomer, "_VERIFIER", console_receipt.Verifier(key_loader=down))
    board = Board([card("DRE-305", raised=_ship() + timedelta(hours=2))],
                  standing=[exclusion])
    sweep(board)
    assert board.lane("DRE-305") == "Intake"
    assert board.posted == []


# --------------------------------------------------------------------------
# 4: epics and their children
# --------------------------------------------------------------------------
def test_an_urgent_epic_is_untouched():
    ship = _ship()
    board = Board([
        card("DRE-401", raised=ship + timedelta(hours=1), title="[EPIC] big work"),
        card("DRE-402", raised=ship + timedelta(hours=1), children=True),
    ])
    sweep(board)
    assert board.moved() == []
    assert board.posted == []


@pytest.mark.parametrize("epic_lane", ["Intake", "Planning", "Green Light", "Backlog"])
def test_an_urgent_child_of_an_epic_that_is_not_running_is_untouched(epic_lane):
    board = Board([card("DRE-403", raised=_ship() + timedelta(hours=1),
                        parent_state=epic_lane)])
    sweep(board)
    assert board.lane("DRE-403") == "Intake"
    assert board.posted == []


def test_an_urgent_child_of_an_in_progress_epic_moves():
    board = Board([card("DRE-404", raised=_ship() + timedelta(hours=1),
                        parent_state="In Progress")])
    sweep(board)
    assert board.lane("DRE-404") == "Planning"
    assert len(board.rule_comments("DRE-404")) == 1


# --------------------------------------------------------------------------
# 5: the cap — three per sweep, oldest-raised first, the rest named
# --------------------------------------------------------------------------
def _five():
    ship = _ship()
    # Filed newest-first on purpose, so "oldest-raised first" is the sort's
    # doing and not the board's order.
    return [card(f"DRE-50{n}", raised=ship + timedelta(hours=n))
            for n in (5, 4, 3, 2, 1)]


def test_five_eligible_three_move_this_sweep_and_two_the_next(capsys):
    board = Board(_five())
    sweep(board)
    assert board.moved() == ["DRE-501", "DRE-502", "DRE-503"], (
        "three per sweep, oldest-raised first")
    out = capsys.readouterr().out
    lines = _fast_path_lines(out)
    waited = [line for line in lines if "waited" in line]
    assert waited, lines
    assert "DRE-504" in waited[0] and "DRE-505" in waited[0], waited
    assert "2" in waited[0], "the log says how many waited"

    board.age(15)  # the next sweep, fifteen minutes on
    sweep(board)
    assert board.moved() == [f"DRE-50{n}" for n in range(1, 6)]
    for n in range(1, 6):
        assert len(board.rule_comments(f"DRE-50{n}")) == 1


# --------------------------------------------------------------------------
# 6: INTAKE_HOLD does not stop it
# --------------------------------------------------------------------------
def test_the_move_happens_with_intake_hold_set(monkeypatch):
    """The signed answer says so: the hold pauses the groomer's drain, and an
    Urgent card waiting behind a pause is the opposite of the rule."""
    monkeypatch.setenv("INTAKE_HOLD", "cutover — paused by the operator")
    board = Board([card("DRE-601", raised=_ship() + timedelta(hours=1))])
    sweep(board)
    assert board.lane("DRE-601") == "Planning"


# --------------------------------------------------------------------------
# 7: two repos, one board, one cap
# --------------------------------------------------------------------------
def _six():
    ship = _ship()
    return [card(f"DRE-70{n}", raised=ship + timedelta(hours=n))
            for n in range(1, 7)]


def test_two_sweeps_from_two_repos_in_the_same_minute_move_three_not_six():
    board = Board(_six())
    with mock.patch.object(reconcile, "REPO_SLUG", "agent-bureau"):
        sweep(board)
    with mock.patch.object(reconcile, "REPO_SLUG", "portico"):
        sweep(board)
    assert board.moved() == ["DRE-701", "DRE-702", "DRE-703"]
    receipts = [i for i, body in board.posted if f"{_tag()}:" in body]
    assert len(receipts) == 3, receipts


def test_a_second_repo_reading_a_board_taken_before_the_first_ones_writes():
    """The harder half: repo B's board read was taken before repo A's writes
    landed, so B still sees all six cards in Intake. The cap is read FRESH off
    the receipts posted in the window, not off B's snapshot — so B moves
    nothing, and posts nothing on the cards A already moved."""
    board = Board(_six())
    before = Board(copy.deepcopy(list(board.cards.values())))
    with mock.patch.object(reconcile, "REPO_SLUG", "agent-bureau"):
        sweep(board)
    with mock.patch.object(reconcile, "REPO_SLUG", "portico"):
        sweep(board, snapshot=before)
    assert board.moved() == ["DRE-701", "DRE-702", "DRE-703"]
    for n in range(1, 7):
        assert len(board.rule_comments(f"DRE-70{n}")) <= 1


def test_the_window_reopens_for_the_next_sweep():
    board = Board(_six())
    sweep(board)
    board.age(15)
    with mock.patch.object(reconcile, "REPO_SLUG", "portico"):
        sweep(board)
    assert board.moved() == [f"DRE-70{n}" for n in range(1, 7)]


# --------------------------------------------------------------------------
# failure handling and the records around the move
# --------------------------------------------------------------------------
def test_a_failed_move_is_a_write_failure_never_a_silent_one():
    board = Board([card("DRE-801", raised=_ship() + timedelta(hours=1))])

    def refuse(*a, **k):
        raise reconcile.linear_ops.LinearError("Linear said no")

    with wired(board), mock.patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=refuse):
        reconcile.advance_urgent_intake()
    assert any("DRE-801" in f for f in reconcile._write_failures)
    reconcile._write_failures.clear()


def test_the_fast_path_is_a_full_sweep_phase_only():
    """The event hooks run the dependency gate alone, as they always have."""
    board = Board([card("DRE-802", raised=_ship() + timedelta(hours=1))])
    reconcile._stale_defects.clear()
    with wired(board), contextlib.ExitStack() as stack:
        for m in _main_mocks():
            stack.enter_context(m)
        reconcile.main(promote_only=True)
    assert board.lane("DRE-802") == "Intake"


def test_the_lane_contract_states_the_urgent_exit_for_intake():
    exit_text = lane_contract.lane("Intake")["clauses"]["exit"]["text"]
    assert "Urgent" in exit_text
    assert "DRE-4150" in exit_text
    assert "INTAKE_HOLD" in exit_text, (
        "the clause must say the hold does not stop this exit — a reader who "
        "knows the hold pauses the drain would otherwise assume it pauses this")


def test_the_act_registry_declares_the_receipt():
    """Declared, and declared honestly: a NEW act reaches the console's
    receipts.py:ACTS before the registry gives it a row (DRE-3091), which is
    another repository's change — so until then the receipt is named in the
    registry's `unconverted` block as an undeclared act, its debt countable.
    Either place satisfies this; silence satisfies neither."""
    doc = json.loads((ROOT / "config" / "pipeline-acts.json").read_text())
    rows = [a for a in doc["acts"] if a["tag"] == reconcile.URGENT_FAST_PATH_OPENER]
    pending = [u for u in doc["unconverted"]
               if u["file"] == "scripts/reconcile.py"
               and "urgent_fast_path_note" in u["anchor"]]
    assert len(rows) + len(pending) == 1, (rows, pending)
    if pending:
        assert pending[0]["kind"] == "undeclared-act"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
