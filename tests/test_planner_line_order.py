"""The planner line takes the next card in the order the CEO set (DRE-5807).

The CEO, 2026-10-04: "In the green planner I can change the order of cards
that are to go into the planner next." The console (DRE-5782) stores the order
he sets by dragging the Overview's In line cards and serves it through the read
door as `GET /api/v1/pipeline/planning-order` →
`{"order": ["DRE-…", …], "set_at", "set_by", "read_at"}`. An empty order means
arrival order.

Until this card, `planner_queue.py` placed a waiting card by the `createdAt` of
its earliest `waiting` receipt since its last release — first come, first
served — and the only way to the front was an approval's `in progress`
trigger. What these pin, in the card's order:

  1. three cards that arrived A, B, C, with an order of [C, A], are claimed
     C, then A, then B;
  2. an approval's `in progress` waiting receipt still goes before C;
  3. a door that answers 500, refuses (401/403), times out, has no such
     endpoint yet (404) or answers outside the contract gives arrival order
     (A, B, C) and says so in ONE line — a read failure never stalls the line;
  4. an `order` naming a card that is not waiting (gone, or already running)
     is skipped, with no error;
  5. every place that computes "your place" uses the same ordering: the
     `waiting` receipt's "place N of M", `next`, and the sweep's backstop
     (DRE-5178) that fires waiting cards into free slots;
  6. the client: what it sends, that it is asked whatever `BUREAU_READ` says,
     and that a failed order read never stops the door for the run's other
     reads (the endpoint is new, and answers 404 until DRE-5782 deploys);
  7. plan.yml's claim and next steps carry the door's address.

No test here reaches the console, GitHub or Linear: the door and the issuer
are `tests/bureau_read_fakes.py` on 127.0.0.1, Linear is the fake board of
`test_planner_queue.py`, and the sweep's seams are `test_planner_queue_sweep.py`'s.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_line_order.py -v
"""
from __future__ import annotations

import contextlib
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE))

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402,F401 — the fake board patches it
import planner_queue as pq  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402
from test_planner_queue import FOUR_SLOTS, Board, config_file, run_cli  # noqa: E402
from test_planner_queue_sweep import (  # noqa: E402,F401 — `_pin` is an autouse fixture
    _four_running,
    _pin,
    _serve,
    _three_waiting,
)

ORDER_PATH = "/api/v1/pipeline/planning-order"
FALLBACK = "the Overview's order could not be read"
A, B, C = "DRE-9101", "DRE-9102", "DRE-9103"
DOOR_ENV = ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE", "BUREAU_PIPELINE_REF",
            "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
            "GITHUB_REPOSITORY")


@pytest.fixture(autouse=True)
def _no_ambient_door(monkeypatch):
    for name in DOOR_ENV:
        monkeypatch.delenv(name, raising=False)
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


@pytest.fixture
def board():
    """The fake Linear board, four slots (the cap these fixtures are written at)."""
    b = Board()
    with config_file(FOUR_SLOTS), b.live():
        yield b


@contextlib.contextmanager
def door(monkeypatch, route=None, *, world=None, mode="off"):
    """A fake console door and OIDC issuer, the client pointed at both.

    `mode` defaults to `off` — today's `BUREAU_READ` across the fleet — on
    purpose: the order is read whatever the board-read rollout switch says.
    """
    with FakeIssuer() as issuer, FakeDoor(world) as fake:
        if route is not None:
            fake.routes["/planning-order"] = route
        for key, value in door_env(door_url=fake.url, issuer=issuer, mode=mode).items():
            monkeypatch.setenv(key, value)
        yield fake


def order_answer(order, *, set_by="sid@dreadnoughtfoundry.com") -> tuple:
    return 200, {"order": list(order), "set_at": "2026-10-04T20:50:00Z",
                 "set_by": set_by, "read_at": "2026-10-04T20:55:00Z"}


def arrivals(b: Board, running: int = 1) -> None:
    """A, B, C waiting, arrived a minute apart in that order, behind `running`
    admitted claims — so `4 - running` slots are free."""
    for i in range(running):
        ident = b.add(f"RUN-{i + 1}")
        b.seed(ident, "claimed", b.clock, run=f"run-{ident}")
        b.tick(60)
    base = b.clock
    for n, ident in enumerate((A, B, C)):
        b.add(ident)
        b.seed(ident, "waiting", base + timedelta(minutes=n), run=f"w-{ident}",
               place=n + 1, of=3)
    b.clock = base + timedelta(minutes=5)


def next_card() -> tuple[str, str]:
    """plan.yml's `Planner slot — next in line` step: (card, stderr)."""
    rc, out, _, err = run_cli(["next"])
    assert rc == 0
    return out["card"], err


def claim_next(b: Board) -> tuple[str, str]:
    """`next`, then the card it names claims its slot — so the line moves."""
    ident, err = next_card()
    if ident:
        b.seed(ident, "claimed", b.clock, run=f"run-{ident}")
        b.tick()
    return ident, err


def claim_all(b: Board, n: int) -> tuple[list, list]:
    cards, errs = [], []
    for _ in range(n):
        ident, err = claim_next(b)
        cards.append(ident)
        errs.append(err)
    return cards, errs


# --------------------------------------------------------------------------- #
# 1. the set order goes first, everyone else in arrival order                 #
# --------------------------------------------------------------------------- #


def test_an_order_of_c_a_over_arrivals_a_b_c_claims_c_then_a_then_b(board, monkeypatch):
    arrivals(board, running=1)
    with door(monkeypatch, order_answer([C, A])) as fake:
        claimed, errs = claim_all(board, 3)
    assert claimed == [C, A, B]
    assert fake.asked("/planning-order"), "the line read the door"
    sent = fake.asked("/planning-order")[0]
    assert sent["path"] == ORDER_PATH
    assert sent["headers"]["authorization"].startswith("Bearer ")
    assert not any(FALLBACK in e for e in errs)


def test_with_no_order_set_the_line_is_arrival_order(board, monkeypatch):
    arrivals(board, running=1)
    with door(monkeypatch, order_answer([])):
        claimed, errs = claim_all(board, 3)
    assert claimed == [A, B, C]
    assert not any(FALLBACK in e for e in errs), "an empty order is not a failure"


def test_the_ledger_itself_is_ordered_so_every_reader_agrees(board):
    arrivals(board, running=4)
    cards = list(board.cards.values())
    assert [w.card for w in pq.ledger(cards, now=board.clock).waiting] == [A, B, C]
    led = pq.ledger(cards, now=board.clock, order=[C, A])
    assert [w.card for w in led.waiting] == [C, A, B]
    # The order changes who is next, never who is running or how many wait.
    plain = pq.ledger(cards, now=board.clock)
    assert [r.card for r in led.running] == [r.card for r in plain.running]
    assert led.free_slots(4) == plain.free_slots(4) == 0


# --------------------------------------------------------------------------- #
# 2. an approval still goes first                                             #
# --------------------------------------------------------------------------- #


def test_an_approval_in_progress_waiter_still_goes_before_the_set_order(board, monkeypatch):
    arrivals(board, running=0)
    epic = board.add("DRE-9199", lane="In Progress")
    board.seed(epic, "waiting", board.clock, run="w-epic", trigger="in progress",
               place=1, of=4)
    board.tick(60)
    with door(monkeypatch, order_answer([C, A])):
        claimed, _ = claim_all(board, 4)
    assert claimed == [epic, C, A, B]


# --------------------------------------------------------------------------- #
# 3. a door that cannot answer gives arrival order, said once                 #
# --------------------------------------------------------------------------- #


FAILURES = {
    "500": (500, {"error": "boom"}),
    "401 refused": (401, {"error": "unauthorized"}),
    "403 refused": (403, {"error": "forbidden"}),
    "404 not deployed yet": (404, {"error": {"code": "NOT_FOUND"}}),
    "503 closed": (503, {"error": "closed"}),
    "order is not a list": (200, {"order": "DRE-9103", "set_at": None, "set_by": None,
                                  "read_at": "2026-10-04T20:55:00Z"}),
    "no order field": (200, {"set_at": None}),
    "not JSON": (200, b"<html>gateway</html>"),
}


@pytest.mark.parametrize("answer", FAILURES.values(), ids=FAILURES.keys())
def test_a_door_that_fails_gives_arrival_order_and_one_log_line(board, monkeypatch, answer):
    arrivals(board, running=1)
    with door(monkeypatch, answer):
        first, err = claim_next(board)
        rest, _ = claim_all(board, 2)
    assert [first, *rest] == [A, B, C]
    assert err.count(FALLBACK) == 1, err
    assert "arrival order" in err


def test_a_door_that_times_out_gives_arrival_order_and_one_log_line(board, monkeypatch):
    monkeypatch.setattr(bureau_read, "TOTAL_TIMEOUT", 0.3)
    arrivals(board, running=1)
    with door(monkeypatch, (*order_answer([C, A]), 1.0)):
        first, err = claim_next(board)
    assert first == A
    assert err.count(FALLBACK) == 1, err
    assert "timeout" in err


def test_no_door_configured_is_arrival_order_and_one_log_line(board):
    """Today's fleet: no `BUREAU_READ_URL` anywhere. The line runs exactly as
    it did before this card, and says why once."""
    arrivals(board, running=1)
    first, err = claim_next(board)
    rest, _ = claim_all(board, 2)
    assert [first, *rest] == [A, B, C]
    assert err.count(FALLBACK) == 1, err


def test_an_order_read_that_raises_anything_never_stalls_the_line(board, monkeypatch):
    arrivals(board, running=1)

    def boom():
        raise RuntimeError("something nobody planned for")

    monkeypatch.setattr(bureau_read, "planning_order", boom)
    first, err = claim_next(board)
    assert first == A
    assert err.count(FALLBACK) == 1, err


def test_a_line_of_one_does_not_ask_the_door(board, monkeypatch):
    """Nothing to order: the next card is the only card."""
    board.add(A)
    board.seed(A, "waiting", board.clock, run="w-a", place=1, of=1)
    board.tick(60)
    with door(monkeypatch, order_answer([A])) as fake:
        ident, err = next_card()
    assert ident == A
    assert fake.asked("/planning-order") == []
    assert FALLBACK not in err


# --------------------------------------------------------------------------- #
# 4. an id that is not waiting is skipped                                     #
# --------------------------------------------------------------------------- #


def test_an_order_naming_cards_that_are_not_waiting_skips_them(board, monkeypatch):
    arrivals(board, running=1)
    # DRE-0404 is not on the board at all; RUN-1 is running, not waiting.
    with door(monkeypatch, order_answer(["DRE-0404", "RUN-1", C])):
        claimed, errs = claim_all(board, 3)
    assert claimed == [C, A, B]
    assert not any(FALLBACK in e for e in errs)


def test_the_order_is_read_case_and_space_insensitively_and_once_per_id(board):
    arrivals(board, running=4)
    cards = list(board.cards.values())
    led = pq.ledger(cards, now=board.clock, order=[f" {B.lower()} ", C, B])
    assert [w.card for w in led.waiting] == [B, C, A]


# --------------------------------------------------------------------------- #
# 5. one ordering: the receipt's place, `next`, and the sweep's backstop      #
# --------------------------------------------------------------------------- #


def _refused_claim(b: Board, ident: str) -> str:
    """A new card claims while all four slots are taken: its `waiting` body."""
    b.add(ident)
    pq.post_claim(linear_ops, ident, run_id=f"run-{ident}", repo="o/n",
                  trigger_state="Planning")
    out = pq.settle_claim(linear_ops, ident, run_id=f"run-{ident}", repo="o/n",
                          trigger_state="Planning")
    assert out["admitted"] == "false"
    return b.nodes(ident)[0]["body"]


def test_the_waiting_receipts_place_follows_the_set_order(board, monkeypatch):
    arrivals(board, running=4)
    with door(monkeypatch, order_answer(["DRE-9104"])):
        body = _refused_claim(board, "DRE-9104")
    assert "waiting for a planner: place 1 of 4" in body


def test_the_place_counts_named_cards_ahead_and_unnamed_behind(board, monkeypatch):
    arrivals(board, running=4)
    with door(monkeypatch, order_answer([C, "DRE-9104", A])):
        body = _refused_claim(board, "DRE-9104")
    # C is ahead of it; A is named after it and B is not named at all.
    assert "waiting for a planner: place 2 of 4" in body
    # And the line, read with the same order, agrees with the receipt.
    led = pq.ledger(list(board.cards.values()), now=board.clock, order=[C, "DRE-9104", A])
    assert [w.card for w in led.waiting] == [C, "DRE-9104", A, B]


def test_without_an_order_a_new_claimant_joins_at_the_back(board):
    arrivals(board, running=4)
    body = _refused_claim(board, "DRE-9104")
    assert "waiting for a planner: place 4 of 4" in body


def test_the_sweeps_backstop_serves_the_card_the_order_names(monkeypatch):
    cards = _four_running() + _three_waiting()
    runs = {"101": "completed", "102": "in_progress", "103": "in_progress",
            "104": "in_progress"}
    with door(monkeypatch, order_answer(["DRE-203"])) as fake:
        world = _serve(cards, runs)
    assert [c["identifier"] for c, _, _ in world.fires] == ["DRE-203"]
    assert fake.asked("/planning-order")


def test_the_sweeps_backstop_with_no_door_serves_the_first_arrival(monkeypatch, capsys):
    cards = _four_running() + _three_waiting()
    runs = {"101": "completed", "102": "in_progress", "103": "in_progress",
            "104": "in_progress"}
    world = _serve(cards, runs)
    assert [c["identifier"] for c, _, _ in world.fires] == ["DRE-201"]
    assert capsys.readouterr().err.count(FALLBACK) == 1


def test_every_caller_orders_through_the_one_reader():
    """`settle_claim`, `next` and the sweep's backstop each read the order
    through `planner_queue`'s one wrapper; nothing else calls the door's
    order endpoint."""
    for path, needle in ((ROOT / "scripts" / "reconcile.py", "planner_queue.ordered_ledger("),
                         (ROOT / "scripts" / "planner_queue.py", "ordered_ledger(")):
        assert needle in path.read_text(encoding="utf-8"), path
    callers = [p.name for p in (ROOT / "scripts").glob("*.py")
               if "bureau_read.planning_order(" in p.read_text(encoding="utf-8")]
    assert callers == ["planner_queue.py"]


# --------------------------------------------------------------------------- #
# 6. the client                                                               #
# --------------------------------------------------------------------------- #


def test_the_client_returns_the_order_and_who_set_it(monkeypatch):
    with door(monkeypatch, order_answer([" dre-3 ", "DRE-1", "DRE-3"])) as fake:
        read = bureau_read.planning_order()
    assert read.order == ["DRE-3", "DRE-1"]
    assert read.set_by == "sid@dreadnoughtfoundry.com"
    assert read.set_at == "2026-10-04T20:50:00Z"
    assert read.read_at == "2026-10-04T20:55:00Z"
    assert [r["path"] for r in fake.requests] == [ORDER_PATH]


def test_the_client_asks_whatever_bureau_read_says(monkeypatch):
    """The mode is the BOARD reads' rollout switch (off → shadow → on, with
    Linear as the fallback). The order has no Linear copy to shadow."""
    for mode in ("off", "shadow", "on"):
        bureau_read.reset_for_tests()
        with door(monkeypatch, order_answer(["DRE-1"]), mode=mode) as fake:
            assert bureau_read.planning_order().order == ["DRE-1"]
            assert fake.asked("/planning-order"), mode


@pytest.mark.parametrize("answer", [FAILURES["404 not deployed yet"], FAILURES["500"],
                                    FAILURES["not JSON"]],
                         ids=["404", "500", "not JSON"])
def test_a_failed_order_read_never_stops_the_door_for_the_runs_board_reads(
        monkeypatch, answer):
    world = {"DRE-1": card("DRE-1", "Todo")}
    with door(monkeypatch, answer, world=world, mode="on") as fake:
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.planning_order()
        assert bureau_read.enabled()
        read = bureau_read.board(["Todo"], max_age=bureau_read.BOARD_MAX_AGE)
    assert [n["identifier"] for n in read.nodes] == ["DRE-1"]
    assert fake.asked("/board")


def test_a_door_already_stopped_this_run_is_not_asked(monkeypatch):
    with door(monkeypatch, order_answer(["DRE-1"]), mode="on") as fake:
        bureau_read._disable("timeout", "the board read timed out")
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.planning_order()
    assert fake.asked("/planning-order") == []


def test_a_pull_request_run_never_asks(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    with door(monkeypatch, order_answer(["DRE-1"])) as fake:
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.planning_order()
    assert caught.value.reason == "event-refused"
    assert fake.requests == []


# --------------------------------------------------------------------------- #
# 7. plan.yml hands the claim and next steps the door's address               #
# --------------------------------------------------------------------------- #


def _plan_step(ident: str) -> dict:
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "plan.yml").read_text(
        encoding="utf-8"))
    return next(s for s in doc["jobs"]["plan"]["steps"] if s.get("id") == ident)


@pytest.mark.parametrize("ident", ["slot", "next"])
def test_the_claim_and_next_steps_carry_the_doors_address(ident):
    env = _plan_step(ident).get("env") or {}
    assert env.get("BUREAU_READ_URL") == "${{ vars.BUREAU_READ_URL }}"
    assert env.get("BUREAU_READ_AUDIENCE") == "${{ vars.BUREAU_READ_AUDIENCE }}"
    assert env.get("BUREAU_PIPELINE_REF") == "${{ inputs.pipeline_ref }}"


def test_reconciles_sweep_step_already_carries_it():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "reconcile.yml").read_text(
        encoding="utf-8"))
    sweep = next(s for job in doc["jobs"].values() for s in job.get("steps", [])
                 if s.get("name") == "Sweep")
    assert sweep["env"]["BUREAU_READ_URL"] == "${{ vars.BUREAU_READ_URL }}"
    assert sweep["env"]["BUREAU_PIPELINE_REF"] == "${{ inputs.pipeline_ref }}"
