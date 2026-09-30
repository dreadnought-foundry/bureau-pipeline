"""The fleet-wide planner slot ledger on Linear (DRE-5176).

Every planner run in every repo shares ONE Linear key, and on 2026-09-28
nineteen `agent-plan` runs released in one minute drained it. The ledger lives
on the cards themselves, as `🎟️ planner-slot:` receipts, and
`scripts/planner_queue.py` owns their grammar, the decision logic and the four
writes. These tests drive it against a FAKE board read — a list of card dicts
in the sweep's shape — with no Linear and no GitHub.

One section per acceptance criterion, in the card's order:

  1. nineteen claim at once: 4 admitted, 15 wait at places 1..15, none lost
  2. the race — two claims written before either reads, both interleavings
  3. the critic's sequential-arrival case, both orders
  4. every interleaving of six cards' writes and reads at cap 2
  5. an admitted run keeps its slot
  6. an `in progress` waiter sorts to the front
  7. the place in line survives a dispatch that finds its slot taken
  8. a `dispatched` receipt reserves a slot until its grace ends
  9. the duplicate claim, and a release closing only the claim it names
 10. the handover — a planner's own re-dispatch inherits its slot
 11. the reason a waiter arrived with is the reason it is re-dispatched with
 12. Green Light is read for its open claim only
 13. the cap is one number, found beside the script, with one reader
 14. a bad config file raises, `check` exits 2, `claim` fails open
 15. the TTL outlasts the plan job, and the wait bound outlasts the TTL
 16. `waited_minutes` / `overdue`
 17. TTL, grace and `in_line`
 18. the CLI's failure paths all exit 0
 19. the act-receipt checks stay green with the one new posting site
 20. the module docstring's boundary answers

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_queue.py -v
"""

from __future__ import annotations

import contextlib
import copy
import io
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import plan_run  # noqa: E402
import planner_queue as pq  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
T0 = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Board:
    """A fake Linear board in the sweep's read shape.

    Comments are stored newest-first, the order Linear's `first:` window
    returns them in, so the module is exercised against the real direction.
    Every post lands at `clock` and then advances it one second, so each
    write has its own `createdAt` unless a test pins one.
    """

    def __init__(self, start: datetime = T0):
        self.cards: dict = {}
        self.clock = start
        self.seq = 0
        self.board_reads = 0
        self.card_reads = 0
        self.queries: list = []
        self.records: dict = {}

    def add(self, ident: str, lane: str = "Planning") -> str:
        self.cards[ident] = {
            "identifier": ident,
            "state": {"name": lane},
            "updatedAt": iso(self.clock),
            "comments": {"nodes": []},
        }
        self.records[ident] = {
            "id": f"uuid-{ident}",
            "identifier": ident,
            "title": f"card {ident}",
            "description": "",
            "labels": {"nodes": [{"name": "agent:planner"}]},
            "children": {"nodes": []},
        }
        return ident

    def move(self, ident: str, lane: str) -> None:
        self.cards[ident]["state"]["name"] = lane

    def tick(self, seconds: float = 1) -> None:
        self.clock += timedelta(seconds=seconds)

    def post(self, ident: str, body: str, at: datetime | None = None,
             cid: str | None = None) -> dict:
        self.seq += 1
        node = {
            "id": cid or f"c-{self.seq:06d}",
            "body": body,
            "createdAt": iso(at or self.clock),
        }
        self.cards[ident]["comments"]["nodes"].insert(0, node)
        return node

    # --- the linear_ops seams -------------------------------------------- #

    def cmd_comment(self, ident: str, body: str, *flags) -> None:
        self.post(ident, body)
        self.tick()
        return None

    def gql_paged(self, query, variables=None, *, connection="issues"):
        self.board_reads += 1
        self.queries.append(query)
        return [copy.deepcopy(c) for c in self.cards.values()]

    def gql(self, query, variables=None):
        ident = (variables or {}).get("id")
        if query == plan_run.CARD_QUERY:
            return {"issue": copy.deepcopy(self.records.get(ident))}
        self.card_reads += 1
        return {"issue": copy.deepcopy(self.cards.get(ident))}

    @contextlib.contextmanager
    def live(self):
        with mock.patch.object(linear_ops, "cmd_comment", self.cmd_comment), \
                mock.patch.object(linear_ops, "gql_paged", self.gql_paged), \
                mock.patch.object(linear_ops, "gql", self.gql), \
                mock.patch.object(pq, "_utcnow", lambda: self.clock):
            yield

    # --- reading back ---------------------------------------------------- #

    def nodes(self, ident: str) -> list:
        return list(self.cards[ident]["comments"]["nodes"])

    def receipts(self, ident: str) -> list:
        out = [pq.parse_receipt(n) for n in self.nodes(ident)]
        return [r for r in out if r is not None]

    def newest(self, ident: str):
        found = self.receipts(ident)
        return found[0] if found else None

    def ledger(self, now: datetime | None = None):
        return pq.ledger(list(self.cards.values()), now=now or self.clock)

    def seed(self, ident: str, state: str, at: datetime, run: str = "",
             trigger: str = "Planning", **extra) -> dict:
        """Post a hand-made receipt at a pinned time."""
        body = pq.format_receipt(
            state, card=ident, run=run or f"seed-{ident}-{state}", repo=REPO,
            trigger=trigger, at=iso(at), **extra)
        return self.post(ident, body, at=at)


def claim_write(board: Board, ident: str, run: str, trigger: str = "Planning"):
    pq.post_claim(linear_ops, ident, run_id=run, repo=REPO, trigger_state=trigger)


def claim_read(board: Board, ident: str, run: str, trigger: str = "Planning",
               reason: str | None = None) -> dict:
    return pq.settle_claim(linear_ops, ident, run_id=run, repo=REPO,
                           trigger_state=trigger, reason=reason)


def run_cli(argv: list) -> tuple[int, dict, str, str]:
    """`main(argv)` with a temp --github-output; returns (rc, outputs, out, err)."""
    fd, path = tempfile.mkstemp()
    os.close(fd)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pq.main(list(argv) + ["--github-output", path]
                         if argv and argv[0] in ("claim", "next") else list(argv))
        with open(path, encoding="utf-8") as f:
            outputs = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    finally:
        os.unlink(path)
    return rc, outputs, out.getvalue(), err.getvalue()


def cli_claim(ident: str, run: str, trigger: str = "Planning", *extra) -> tuple:
    return run_cli(["claim", ident, "--run-id", run, "--repo", REPO,
                    "--trigger-state", trigger, *extra])


def fill_running(board: Board, n: int, prefix: str = "RUN") -> list:
    """`n` cards each holding an admitted claim, posted a minute apart."""
    idents = []
    for i in range(n):
        ident = board.add(f"{prefix}-{i + 1}")
        board.seed(ident, "claimed", board.clock, run=f"run-{ident}")
        board.tick(60)
        idents.append(ident)
    return idents


@contextlib.contextmanager
def config_file(doc, raw: str | None = None):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(raw if raw is not None else json.dumps(doc))
    try:
        with mock.patch.dict(os.environ, {"PLANNER_QUEUE_CONFIG": path}):
            yield path
    finally:
        os.unlink(path)


#: The claim TTL follows the plan job's clock (`Bounds` below): 115 since
#: DRE-5288 sized the two re-plan ceilings per plan and the job clock rose with
#: them.
COMMITTED = {"max_running": 2, "claim_ttl_minutes": 115,
             "dispatched_grace_minutes": 10, "waiting_max_minutes": 360}

#: The ledger's behavior is tested at the four slots it was written against
#: (DRE-5176). The committed number is `TheCap`'s contract alone: DRE-5326
#: dropped it to two on 2026-09-30, when four planners out-spent Linear's
#: refill, and the rules below do not change with the number. The same for the
#: TTL: the lifetimes below are written either side of 105 minutes, and the
#: committed TTL moving with the plan job's clock (DRE-5288) does not move them.
FOUR_SLOTS = dict(COMMITTED, max_running=4, claim_ttl_minutes=105)


class _Base(unittest.TestCase):
    def setUp(self):
        # A pinned four-slot file, whatever the environment running the suite
        # says; `TheCap` reads the committed one itself.
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        os.environ.pop("PLANNER_QUEUE_CONFIG", None)
        self.addCleanup(patcher.stop)
        pinned = config_file(FOUR_SLOTS)
        pinned.__enter__()
        self.addCleanup(pinned.__exit__, None, None, None)
        self.board = Board()
        live = self.board.live()
        live.__enter__()
        self.addCleanup(live.__exit__, None, None, None)


# --------------------------------------------------------------------------- #
# 1. nineteen at once                                                          #
# --------------------------------------------------------------------------- #

PLACE_RE = re.compile(r"waiting for a planner: place (\d+) of (\d+)")


class NineteenAtOnce(_Base):
    def _nineteen(self, read_order):
        idents = [self.board.add(f"DRE-{9000 + i}") for i in range(19)]
        for ident in idents:
            claim_write(self.board, ident, f"run-{ident}")
        results = {}
        for i in read_order:
            ident = idents[i]
            results[ident] = claim_read(self.board, ident, f"run-{ident}")
        return idents, results

    def _assert_shape(self, idents, results, cap=4):
        admitted = [i for i in idents if results[i]["admitted"] == "true"]
        self.assertEqual(len(admitted), cap)
        waiting = [i for i in idents if results[i]["admitted"] == "false"]
        self.assertEqual(len(waiting), 19 - cap)
        places = []
        for ident in waiting:
            newest = self.board.nodes(ident)[0]["body"]
            self.assertTrue(newest.startswith(pq.SLOT_MARK))
            self.assertIn(" waiting ", newest.splitlines()[0] + " ")
            m = PLACE_RE.search(newest)
            self.assertIsNotNone(m, newest)
            places.append(int(m.group(1)))
            self.assertEqual(int(m.group(2)), 19 - cap)
        self.assertEqual(sorted(places), list(range(1, 19 - cap + 1)))
        # Every one of the nineteen carries a planner-slot receipt: none is
        # canceled, skipped or missing.
        for ident in idents:
            self.assertTrue(self.board.receipts(ident), ident)
        led = self.board.ledger()
        self.assertEqual(len(led.running), cap)
        self.assertEqual(len(led.waiting), 19 - cap)
        # The line's order is the places the receipts announced.
        for pos, w in enumerate(led.waiting, start=1):
            body = self.board.nodes(w.card)[0]["body"]
            self.assertIn(f"place {pos} of {19 - cap}", body)

    def test_claim_order_reads(self):
        idents, results = self._nineteen(range(19))
        self._assert_shape(idents, results)
        # The four admitted are the four earliest claims.
        self.assertEqual([i for i in idents if results[i]["admitted"] == "true"],
                         idents[:4])

    def test_any_read_order(self):
        for seed in (1, 7, 42):
            with self.subTest(seed=seed):
                self.board = Board()
                with self.board.live():
                    order = list(range(19))
                    random.Random(seed).shuffle(order)
                    idents, results = self._nineteen(order)
                    self._assert_shape(idents, results)

    def test_nineteen_through_the_cli(self):
        idents = [self.board.add(f"DRE-{9100 + i}") for i in range(19)]
        outs = [cli_claim(ident, f"run-{ident}") for ident in idents]
        self.assertTrue(all(rc == 0 for rc, *_ in outs))
        self.assertEqual(sum(o["admitted"] == "true" for _, o, *_ in outs), 4)
        self.assertEqual(sorted(int(o["place"]) for _, o, *_ in outs
                                if o["admitted"] == "false"), list(range(1, 16)))


# --------------------------------------------------------------------------- #
# 2. the race                                                                  #
# --------------------------------------------------------------------------- #


class Race(_Base):
    def _race(self, reads_first, same_second):
        fill_running(self.board, 3)
        a, b = self.board.add("DRE-A"), self.board.add("DRE-B")
        if same_second:
            at = self.board.clock
            self.board.post(a, pq.format_receipt(
                "claimed", card=a, run="run-a", repo=REPO, trigger="Planning",
                at=iso(at)), at=at, cid="c-zz-a")
            self.board.post(b, pq.format_receipt(
                "claimed", card=b, run="run-b", repo=REPO, trigger="Planning",
                at=iso(at)), at=at, cid="c-zz-b")
            self.board.tick()
        else:
            claim_write(self.board, a, "run-a")
            claim_write(self.board, b, "run-b")
        out = {}
        for ident in reads_first:
            out[ident] = claim_read(self.board, ident, f"run-{ident[-1].lower()}")
        return out

    def test_both_interleavings_same_admission(self):
        for same_second in (False, True):
            for order in (("DRE-A", "DRE-B"), ("DRE-B", "DRE-A")):
                with self.subTest(order=order, same_second=same_second):
                    self.board = Board()
                    with self.board.live():
                        out = self._race(order, same_second)
                        # A claimed first (by createdAt, or by comment id at a
                        # tie): A takes the fourth slot, B waits.
                        self.assertEqual(out["DRE-A"]["admitted"], "true")
                        self.assertEqual(out["DRE-B"]["admitted"], "false")
                        self.assertEqual(len(self.board.ledger().running), 4)

    def test_tie_broken_by_comment_id(self):
        fill_running(self.board, 3)
        a, b = self.board.add("DRE-A"), self.board.add("DRE-B")
        at = self.board.clock
        # B's comment id sorts first: B holds the earlier claim.
        self.board.post(a, pq.format_receipt(
            "claimed", card=a, run="run-a", repo=REPO, trigger="Planning",
            at=iso(at)), at=at, cid="c-2")
        self.board.post(b, pq.format_receipt(
            "claimed", card=b, run="run-b", repo=REPO, trigger="Planning",
            at=iso(at)), at=at, cid="c-1")
        self.board.tick()
        self.assertEqual(claim_read(self.board, a, "run-a")["admitted"], "false")
        self.assertEqual(claim_read(self.board, b, "run-b")["admitted"], "true")


# --------------------------------------------------------------------------- #
# 3. the critic's sequential-arrival case                                      #
# --------------------------------------------------------------------------- #


class SequentialArrival(_Base):
    def _fixture(self):
        fill_running(self.board, 3)
        b = self.board.add("DRE-B")
        a = self.board.add("DRE-A")
        base = self.board.clock
        self.board.seed(b, "waiting", base, place=1, of=1)  # line entry T0
        self.board.seed(b, "dispatched", base + timedelta(minutes=5), run="sweep")
        self.board.clock = base + timedelta(minutes=6)
        return a, b, base

    def test_a_reads_before_b_claims(self):
        a, b, _ = self._fixture()
        claim_write(self.board, a, "run-a")
        self.assertEqual(claim_read(self.board, a, "run-a")["admitted"], "false")
        claim_write(self.board, b, "run-b")
        self.assertEqual(claim_read(self.board, b, "run-b")["admitted"], "true")
        led = self.board.ledger()
        self.assertEqual(len(led.running), 4)
        self.assertEqual({r.card for r in led.running} & {a, b}, {b})

    def test_b_claims_between_a_claim_and_a_read(self):
        a, b, base = self._fixture()
        claim_write(self.board, a, "run-a")
        claim_write(self.board, b, "run-b")
        self.assertEqual(claim_read(self.board, a, "run-a")["admitted"], "true")
        out = claim_read(self.board, b, "run-b")
        self.assertEqual(out["admitted"], "false")
        led = self.board.ledger()
        self.assertEqual(len(led.running), 4)
        self.assertEqual(led.waiting[0].card, b)
        self.assertEqual(pq.line_entry(self.board.nodes(b), now=self.board.clock),
                         iso(base))

    def test_neither_order_ever_shows_five(self):
        for order in ("a-first", "b-between"):
            with self.subTest(order=order):
                self.board = Board()
                with self.board.live():
                    a, b, _ = self._fixture()
                    steps = ([("w", a), ("r", a), ("w", b), ("r", b)]
                             if order == "a-first" else
                             [("w", a), ("w", b), ("r", a), ("r", b)])
                    admitted, pending = 3, set()
                    for kind, ident in steps:
                        run = f"run-{ident[-1].lower()}"
                        if kind == "w":
                            # Between a claim's write and its read the claim
                            # is open by definition — undecided, not admitted.
                            claim_write(self.board, ident, run)
                            pending.add(ident)
                            continue
                        out = claim_read(self.board, ident, run)
                        pending.discard(ident)
                        admitted += out["admitted"] == "true"
                        self.assertLessEqual(admitted, 4)
                        self.assertLessEqual(len(self.board.ledger().running),
                                             4 + len(pending))
                    self.assertEqual(admitted, 4)
                    self.assertEqual(len(self.board.ledger().running), 4)


# --------------------------------------------------------------------------- #
# 4. every interleaving, six cards, cap 2                                      #
# --------------------------------------------------------------------------- #


def canonical_interleavings(n: int):
    """Every sequence of n writes and n reads, each read after its own write,
    with the writes in card order.

    Six identical cards give 12!/2^6 = 7,484,400 orderings, but the fixture
    is symmetric under renaming the cards — every card starts empty in the
    same lane and every event lands at its own second, so no identifier ever
    breaks a tie. Every ordering is therefore a renaming of one whose writes
    come in card order, and these 10,395 are all of them up to that renaming.
    """
    def walk(prefix, written, read):
        if read == (1 << n) - 1:
            yield list(prefix)
            return
        if written < n:
            prefix.append(("w", written))
            yield from walk(prefix, written + 1, read)
            prefix.pop()
        for i in range(written):
            if not read & (1 << i):
                prefix.append(("r", i))
                yield from walk(prefix, written, read | (1 << i))
                prefix.pop()
    yield from walk([], 0, 0)


class EveryInterleaving(_Base):
    def test_count_is_complete(self):
        self.assertEqual(sum(1 for _ in canonical_interleavings(3)), 15)
        self.assertEqual(sum(1 for _ in canonical_interleavings(6)), 10395)

    def test_at_most_cap_admitted_in_every_ordering(self):
        cfg = dict(COMMITTED, max_running=2)
        with config_file(cfg):
            config = pq.load()
            idents = [f"DRE-{700 + i}" for i in range(6)]
            orderings = 0
            for events in canonical_interleavings(6):
                orderings += 1
                board = Board()
                for ident in idents:
                    board.add(ident)
                admitted = []
                with mock.patch.object(linear_ops, "cmd_comment", board.cmd_comment), \
                        mock.patch.object(pq, "_utcnow", lambda: board.clock):
                    for kind, i in events:
                        ident = idents[i]
                        if kind == "w":
                            claim_write(board, ident, f"run-{i}")
                        else:
                            out = pq.settle_claim(
                                linear_ops, ident, run_id=f"run-{i}", repo=REPO,
                                trigger_state="Planning",
                                cards=list(board.cards.values()), config=config)
                            if out["admitted"] == "true":
                                admitted.append(ident)
                self.assertLessEqual(len(admitted), 2, events)
                for ident in idents:
                    if ident not in admitted:
                        self.assertEqual(board.newest(ident).state, "waiting",
                                         (events, ident))
            self.assertEqual(orderings, 10395)

    def test_release_then_the_next_claim_is_admitted(self):
        with config_file(dict(COMMITTED, max_running=2)):
            idents = [self.board.add(f"DRE-{800 + i}") for i in range(6)]
            for ident in idents:
                claim_write(self.board, ident, f"run-{ident}")
            outs = {i: claim_read(self.board, i, f"run-{i}") for i in idents}
            self.assertEqual(sum(o["admitted"] == "true" for o in outs.values()), 2)
            first = idents[0]
            pq.post_released(linear_ops, first, run_id=f"run-{first}", repo=REPO,
                             trigger_state="Planning", because="finished")
            nxt = pq.next_in_line(self.board.ledger())
            self.assertEqual(nxt.card, idents[2])
            claim_write(self.board, idents[2], "run-again")
            self.assertEqual(claim_read(self.board, idents[2], "run-again")["admitted"],
                             "true")


# --------------------------------------------------------------------------- #
# 5. an admitted run keeps its slot                                            #
# --------------------------------------------------------------------------- #


class AdmittedKeepsSlot(_Base):
    def test_only_release_ttl_or_run_gone_remove_a_claim(self):
        idents = [self.board.add(f"DRE-{50 + i}") for i in range(5)]
        for ident in idents:
            claim_write(self.board, ident, f"run-{ident}")
            claim_read(self.board, ident, f"run-{ident}")
        four = set(idents[:4])
        self.assertEqual(self.board.newest(idents[4]).state, "waiting")
        start = self.board.clock
        for minutes in (1, 30, 60, 104):
            led = self.board.ledger(now=start + timedelta(minutes=minutes))
            self.assertEqual({r.card for r in led.running}, four, minutes)
        # A later claimant's read does not remove any of them either.
        late = self.board.add("DRE-LATE")
        claim_write(self.board, late, "run-late")
        self.assertEqual(claim_read(self.board, late, "run-late")["admitted"], "false")
        self.assertEqual({r.card for r in self.board.ledger().running}, four)
        # released
        pq.post_released(linear_ops, idents[0], run_id=f"run-{idents[0]}",
                         repo=REPO, trigger_state="Planning", because="finished")
        self.assertNotIn(idents[0], {r.card for r in self.board.ledger().running})
        # run-gone (the sweep's check)
        pq.post_released(linear_ops, idents[1], run_id=f"run-{idents[1]}",
                         repo=REPO, trigger_state="Planning", because="run-gone")
        self.assertNotIn(idents[1], {r.card for r in self.board.ledger().running})
        # the TTL
        led = self.board.ledger(now=start + timedelta(minutes=106))
        self.assertEqual({r.card for r in led.running} & {idents[2], idents[3]}, set())
        self.assertEqual({r.card for r in pq.expired_claims(
            led, start + timedelta(minutes=106))}, {idents[2], idents[3]})


# --------------------------------------------------------------------------- #
# 6. activate route to the front                                               #
# --------------------------------------------------------------------------- #


class FrontOfLine(_Base):
    def test_in_progress_waiter_is_first_and_the_rest_keep_order(self):
        fill_running(self.board, 4)
        three = []
        for i in range(3):
            ident = self.board.add(f"DRE-W{i}")
            claim_write(self.board, ident, f"run-{ident}")
            claim_read(self.board, ident, f"run-{ident}")
            three.append(ident)
        before = [w.card for w in self.board.ledger().waiting]
        self.assertEqual(before, three)
        epic = self.board.add("DRE-EPIC", lane="In Progress")
        claim_write(self.board, epic, "run-epic", trigger="in progress")
        out = claim_read(self.board, epic, "run-epic", trigger="in progress")
        self.assertEqual(out["admitted"], "false")
        self.assertEqual(out["place"], "1")
        after = [w.card for w in self.board.ledger().waiting]
        self.assertEqual(after, [epic] + three)


# --------------------------------------------------------------------------- #
# 7. the place in line                                                         #
# --------------------------------------------------------------------------- #


class PlaceInLine(_Base):
    def _fixture(self):
        fill_running(self.board, 4)
        base = self.board.clock
        a, b, c = (self.board.add(x) for x in ("DRE-PA", "DRE-PB", "DRE-PC"))
        self.board.seed(a, "waiting", base, place=1, of=3)
        self.board.seed(b, "waiting", base + timedelta(minutes=2), place=2, of=3)
        self.board.seed(c, "waiting", base + timedelta(minutes=3), place=3, of=3)
        self.board.seed(a, "dispatched", base + timedelta(minutes=5), run="sweep")
        self.board.clock = base + timedelta(minutes=6)
        return a, b, c, base

    def test_rewaiting_keeps_the_original_line_entry(self):
        a, b, c, base = self._fixture()
        claim_write(self.board, a, "run-a")
        out = claim_read(self.board, a, "run-a")
        self.assertEqual(out["admitted"], "false")
        self.assertIn("waiting for a planner: place 1 of ",
                      self.board.nodes(a)[0]["body"])
        led = self.board.ledger()
        self.assertEqual([w.card for w in led.waiting], [a, b, c])
        self.assertEqual(pq.line_entry(self.board.nodes(a), now=self.board.clock),
                         iso(base))

    def test_fresh_arrival_one_second_earlier_wins_the_slot(self):
        a, b, c, base = self._fixture()
        # One of the four finishes: one slot is free.
        pq.post_released(linear_ops, "RUN-1", run_id="run-RUN-1", repo=REPO,
                         trigger_state="Planning", because="finished")
        t6 = base + timedelta(minutes=6)
        fresh = self.board.add("DRE-FRESH")
        self.board.post(fresh, pq.format_receipt(
            "claimed", card=fresh, run="run-fresh", repo=REPO, trigger="Planning",
            at=iso(t6 - timedelta(seconds=1))), at=t6 - timedelta(seconds=1))
        self.board.post(a, pq.format_receipt(
            "claimed", card=a, run="run-a", repo=REPO, trigger="Planning",
            at=iso(t6)), at=t6)
        self.board.clock = t6 + timedelta(seconds=1)
        self.assertEqual(claim_read(self.board, fresh, "run-fresh")["admitted"], "true")
        self.assertEqual(claim_read(self.board, a, "run-a")["admitted"], "false")
        led = self.board.ledger()
        self.assertEqual(led.waiting[0].card, a)
        self.assertEqual(pq.line_entry(self.board.nodes(a), now=self.board.clock),
                         iso(base))
        self.assertIsNone(pq.next_in_line(led))
        pq.post_released(linear_ops, "RUN-2", run_id="run-RUN-2", repo=REPO,
                         trigger_state="Planning", because="finished")
        self.assertEqual(pq.next_in_line(self.board.ledger()).card, a)


# --------------------------------------------------------------------------- #
# 8. the reserved slot                                                         #
# --------------------------------------------------------------------------- #


class ReservedSlot(_Base):
    def _fixture(self, dispatched_age_minutes):
        fill_running(self.board, 3)
        now = self.board.clock + timedelta(minutes=30)
        d = self.board.add("DRE-D")
        self.board.seed(d, "waiting", now - timedelta(minutes=25), place=1, of=4)
        self.board.seed(d, "dispatched", now - timedelta(minutes=dispatched_age_minutes),
                        run="sweep")
        for i in range(3):
            w = self.board.add(f"DRE-W{i}")
            self.board.seed(w, "waiting", now - timedelta(minutes=20 - i))
        self.board.clock = now
        return d

    def test_in_grace_reserves(self):
        d = self._fixture(dispatched_age_minutes=3)
        led = self.board.ledger()
        self.assertEqual([r.card for r in led.reserved], [d])
        self.assertEqual(led.free_slots(4), 0)
        self.assertIsNone(pq.next_in_line(led))
        self.assertNotIn(d, [w.card for w in led.waiting])

    def test_past_grace_waits_again_at_its_own_entry(self):
        d = self._fixture(dispatched_age_minutes=11)
        led = self.board.ledger()
        self.assertEqual(led.reserved, [])
        self.assertEqual(led.free_slots(4), 1)
        nxt = pq.next_in_line(led)
        self.assertEqual(nxt.card, d)
        self.assertEqual(led.waiting[0].card, d)
        self.assertEqual(pq.line_entry(self.board.nodes(d), now=self.board.clock),
                         iso(self.board.clock - timedelta(minutes=25)))


# --------------------------------------------------------------------------- #
# 9. the duplicate                                                             #
# --------------------------------------------------------------------------- #


class Duplicate(_Base):
    def test_two_claims_one_card(self):
        card = self.board.add("DRE-DUP")
        claim_write(self.board, card, "run-1")
        # The CLI posts run-2's claim first, then reads: two claims, two runs.
        rc, out, stdout, _ = cli_claim(card, "run-2")
        self.assertEqual(rc, 0)
        self.assertEqual(out["admitted"], "false")
        self.assertEqual(out["duplicate"], "true")
        newest = self.board.newest(card)
        self.assertEqual((newest.state, newest.run, newest.because),
                         ("released", "run-2", "duplicate"))
        self.assertFalse(any(r.state == "waiting" for r in self.board.receipts(card)))
        nodes = self.board.nodes(card)
        self.assertEqual(pq.open_claim(nodes, now=self.board.clock).run, "run-1")
        self.assertEqual(pq.state(nodes, now=self.board.clock), "claimed")
        self.assertEqual([r.card for r in self.board.ledger().running], [card])

    def test_duplicate_through_settle(self):
        card = self.board.add("DRE-DUP2")
        claim_write(self.board, card, "run-1")
        claim_write(self.board, card, "run-2")
        led = self.board.ledger()
        self.assertEqual([(r.card, r.run) for r in led.running], [(card, "run-1")])
        out = claim_read(self.board, card, "run-2")
        self.assertEqual((out["admitted"], out["duplicate"]), ("false", "true"))
        self.assertEqual(claim_read(self.board, card, "run-1")["admitted"], "true")
        self.assertEqual(pq.open_claim(self.board.nodes(card)).run, "run-1")


# --------------------------------------------------------------------------- #
# 10. the handover                                                             #
# --------------------------------------------------------------------------- #


class Handover(_Base):
    def _four_running_with_sender(self):
        others = fill_running(self.board, 3)
        card = self.board.add("DRE-EP", lane="In Progress")
        self.board.seed(card, "claimed", self.board.clock, run="run-S",
                        trigger="in progress")
        self.board.tick(60)
        return others, card

    def test_i_sender_holds_the_claim(self):
        others, card = self._four_running_with_sender()
        waiter = self.board.add("DRE-WAIT")
        self.board.seed(waiter, "waiting", self.board.clock)
        self.board.tick()
        reads = self.board.board_reads
        rc, out, _, _ = cli_claim(card, "run-C", "in progress",
                                  "--sent-by-run", "run-S", "--reason", "re-review")
        self.assertEqual(rc, 0)
        self.assertEqual((out["admitted"], out["inherited"]), ("true", "true"))
        self.assertEqual(self.board.board_reads, reads, "the board read was called")
        newest = self.board.newest(card)
        self.assertEqual((newest.state, newest.run, newest.from_run),
                         ("claimed", "run-C", "run-S"))
        self.assertIn(" · run run-C · ", self.board.nodes(card)[0]["body"])
        self.assertIn(" · from run run-S · ", self.board.nodes(card)[0]["body"])
        nodes = self.board.nodes(card)
        self.assertEqual(pq.open_claim(nodes, now=self.board.clock).run, "run-C")
        led = self.board.ledger()
        self.assertEqual(sorted(r.card for r in led.running), sorted(others + [card]))
        before = ([r.card for r in led.running], led.free_slots(4),
                  pq.next_in_line(led))
        # S's own release at its end closes nothing still open.
        pq.post_released(linear_ops, card, run_id="run-S", repo=REPO,
                         trigger_state="in progress", because="finished")
        led = self.board.ledger()
        self.assertEqual(([r.card for r in led.running], led.free_slots(4),
                          pq.next_in_line(led)), before)
        # C's own release frees the slot.
        pq.post_released(linear_ops, card, run_id="run-C", repo=REPO,
                         trigger_state="in progress", because="finished")
        led = self.board.ledger()
        self.assertNotIn(card, [r.card for r in led.running])
        self.assertEqual(led.free_slots(4), 1)
        self.assertEqual(pq.next_in_line(led).card, waiter)

    def test_inherited_slot_keeps_the_senders_place_in_claim_order(self):
        # X claims between S's claim and the handover, and reads after it: X
        # must still count S's slot, now C's, as earlier than its own.
        fill_running(self.board, 3)
        card = self.board.add("DRE-EP", lane="In Progress")
        self.board.seed(card, "claimed", self.board.clock, run="run-S",
                        trigger="in progress")
        self.board.tick(60)
        x = self.board.add("DRE-X")
        claim_write(self.board, x, "run-x")
        rc, out, _, _ = cli_claim(card, "run-C", "in progress", "--sent-by-run", "run-S")
        self.assertEqual(out["inherited"], "true")
        self.assertEqual(claim_read(self.board, x, "run-x")["admitted"], "false")
        self.assertLessEqual(len(self.board.ledger().running), 4)

    def test_ii_sender_already_released(self):
        others, card = self._four_running_with_sender()
        pq.post_released(linear_ops, card, run_id="run-S", repo=REPO,
                         trigger_state="in progress", because="finished")
        # the freed slot went to another card
        extra = self.board.add("DRE-EXTRA")
        claim_write(self.board, extra, "run-extra")
        claim_read(self.board, extra, "run-extra")
        waiter = self.board.add("DRE-WAIT")
        self.board.seed(waiter, "waiting", self.board.clock)
        self.board.tick()
        rc, out, _, _ = cli_claim(card, "run-C", "in progress",
                                  "--sent-by-run", "run-S", "--reason", "re-review")
        self.assertEqual(rc, 0)
        self.assertEqual((out["admitted"], out["inherited"]), ("false", "false"))
        self.assertEqual(out["place"], "1")
        newest = self.board.newest(card)
        self.assertEqual((newest.state, newest.reason, newest.trigger),
                         ("waiting", "re-review", "in progress"))
        self.assertIn("waiting for a planner: place 1 of ", self.board.nodes(card)[0]["body"])
        led = self.board.ledger()
        self.assertEqual(led.waiting[0].card, card)
        self.assertLessEqual(len(led.running), 4)

    def test_ii_slot_free_is_an_ordinary_admission(self):
        fill_running(self.board, 2)
        card = self.board.add("DRE-EP", lane="In Progress")
        self.board.seed(card, "claimed", self.board.clock, run="run-S",
                        trigger="in progress")
        self.board.tick()
        pq.post_released(linear_ops, card, run_id="run-S", repo=REPO,
                         trigger_state="in progress", because="finished")
        rc, out, _, _ = cli_claim(card, "run-C", "in progress", "--sent-by-run", "run-S")
        self.assertEqual((out["admitted"], out["inherited"]), ("true", "false"))
        newest = self.board.newest(card)
        self.assertEqual((newest.state, newest.run, newest.from_run),
                         ("claimed", "run-C", None))
        self.assertNotIn("from run", self.board.nodes(card)[0]["body"])

    def test_unreadable_card_still_posts_the_claim(self):
        _, card = self._four_running_with_sender()

        def boom(*a, **k):
            raise linear_ops.LinearError("card read failed")

        with mock.patch.object(linear_ops, "gql", boom):
            rc, out, stdout, _ = cli_claim(card, "run-C", "in progress",
                                           "--sent-by-run", "run-S")
        self.assertEqual(rc, 0)
        self.assertIn("::warning::", stdout)
        # The run is on the ledger: its claim was posted, as an ordinary one,
        # and the board read still sees its sender's slot passing on — not a
        # duplicate, which would lose the review.
        claims = [r for r in self.board.receipts(card)
                  if r.state == "claimed" and r.run == "run-C"]
        self.assertEqual(len(claims), 1)
        self.assertIsNone(claims[0].from_run)
        self.assertEqual((out["admitted"], out["duplicate"], out["inherited"]),
                         ("true", "false", "true"))
        self.assertFalse(any(r.state == "released" for r in self.board.receipts(card)))
        led = self.board.ledger()
        self.assertEqual(len(led.running), 4)
        # S's release at its end leaves C's claim holding the card's one slot.
        pq.post_released(linear_ops, card, run_id="run-S", repo=REPO,
                         trigger_state="in progress", because="finished")
        led = self.board.ledger()
        self.assertIn(card, [r.card for r in led.running])
        self.assertEqual(len(led.running), 4)

    def test_iii_sent_by_a_run_with_no_claim(self):
        fill_running(self.board, 4)
        card = self.board.add("DRE-EP", lane="In Progress")
        rc, out, _, _ = cli_claim(card, "run-C", "in progress",
                                  "--sent-by-run", "run-R", "--reason", "re-review")
        self.assertEqual(rc, 0)
        self.assertEqual((out["admitted"], out["inherited"]), ("false", "false"))
        self.assertNotIn("from run", self.board.nodes(card)[1]["body"])
        self.assertEqual(self.board.ledger().waiting[0].card, card)
        self.assertLessEqual(len(self.board.ledger().running), 4)


# --------------------------------------------------------------------------- #
# 11. the reason                                                               #
# --------------------------------------------------------------------------- #


class Reason(_Base):
    def test_reason_round_trip(self):
        fill_running(self.board, 4)
        card = self.board.add("DRE-RR")
        rc, out, _, _ = cli_claim(card, "run-rr", "Planning", "--reason", "re-run")
        self.assertEqual(out["admitted"], "false")
        self.assertIn(" · reason re-run · ", self.board.nodes(card)[0]["body"])
        pq.post_released(linear_ops, "RUN-1", run_id="run-RUN-1", repo=REPO,
                         trigger_state="Planning", because="finished")
        rc, out, _, _ = run_cli(["next"])
        self.assertEqual(rc, 0)
        self.assertEqual(out["card"], card)
        self.assertEqual(out["reason"], "re-run")
        self.assertEqual(out["trigger_state"], "Planning")
        self.assertEqual((out["repo"], out["owner"], out["name"]),
                         (REPO, "dreadnought-foundry", "bureau-pipeline"))

        calls = []

        def fire(card_record, repo, **kwargs):
            calls.append((card_record, repo, kwargs))
            return True, ""

        with mock.patch.object(plan_run, "fire", fire):
            rc, *_ = run_cli(["dispatch", card, "--run-id", "run-sweep",
                              "--repo", REPO, "--trigger-state", "Planning",
                              "--reason", "re-run"])
        self.assertEqual(rc, 0)
        record, repo, kwargs = calls[0]
        self.assertEqual(record, self.board.records[card])
        self.assertEqual(kwargs.get("reason"), "re-run")
        self.assertNotIn("sent_by_run", kwargs)
        self.assertNotIn("sent_by_run", plan_run.payload(record, **kwargs))
        newest = self.board.newest(card)
        self.assertEqual((newest.state, newest.run, newest.reason),
                         ("dispatched", "run-sweep", "re-run"))

    def test_no_reason(self):
        fill_running(self.board, 4)
        card = self.board.add("DRE-NR")
        cli_claim(card, "run-nr", "Planning", "--reason", "")
        self.assertIsNone(self.board.newest(card).reason)
        self.assertNotIn("reason", self.board.nodes(card)[0]["body"].splitlines()[0])
        pq.post_released(linear_ops, "RUN-1", run_id="run-RUN-1", repo=REPO,
                         trigger_state="Planning", because="finished")
        rc, out, _, _ = run_cli(["next"])
        self.assertEqual((out["card"], out["reason"]), (card, ""))
        calls = []
        with mock.patch.object(plan_run, "fire",
                               lambda c, r, **k: (calls.append((c, r, k)) or (True, ""))):
            run_cli(["dispatch", card, "--run-id", "run-sweep", "--repo", REPO,
                     "--trigger-state", "Planning", "--reason", ""])
        record, _, kwargs = calls[0]
        self.assertIsNone(kwargs.get("reason"))
        self.assertNotIn("reason", plan_run.payload(record, **kwargs))

    def test_the_earlier_grammar_parses_as_before(self):
        body = ("🎟️ planner-slot: waiting · card DRE-1 · run 99 · repo o/n · "
                "trigger Planning · at 2026-09-28T12:00:00Z\n"
                "waiting for a planner: place 2 of 5")
        r = pq.parse_receipt(body)
        self.assertEqual((r.state, r.card, r.run, r.repo, r.trigger, r.at),
                         ("waiting", "DRE-1", "99", "o/n", "Planning",
                          "2026-09-28T12:00:00Z"))
        self.assertIsNone(r.from_run)
        self.assertIsNone(r.reason)
        self.assertIsNone(r.because)
        full = ("🎟️ planner-slot: claimed · card DRE-1 · run 7 · repo o/n · "
                "trigger in progress · from run 6 · at 2026-09-28T12:00:00Z")
        r = pq.parse_receipt(full)
        self.assertEqual((r.trigger, r.from_run), ("in progress", "6"))
        rel = ("🎟️ planner-slot: released · card DRE-1 · run 7 · repo o/n · "
               "trigger Planning · at 2026-09-28T12:00:00Z · because duplicate")
        self.assertEqual(pq.parse_receipt(rel).because, "duplicate")
        self.assertIsNone(pq.parse_receipt("planner-slot: claimed, said a person"))
        self.assertIsNone(pq.parse_receipt("⏳ 1/5 plan"))


# --------------------------------------------------------------------------- #
# 12. Green Light                                                              #
# --------------------------------------------------------------------------- #


class GreenLight(_Base):
    def test_open_claim_counted_waiting_ignored(self):
        fill_running(self.board, 2)
        gl = self.board.add("DRE-GL", lane="Green Light")
        self.board.seed(gl, "claimed", self.board.clock, run="run-gl")
        self.board.tick(60)
        gw = self.board.add("DRE-GW", lane="Green Light")
        self.board.seed(gw, "waiting", self.board.clock)
        self.board.tick(60)
        led = self.board.ledger()
        self.assertIn(gl, [r.card for r in led.running])
        self.assertNotIn(gw, [r.card for r in led.running])
        self.assertNotIn(gw, [w.card for w in led.waiting])
        self.assertEqual(led.free_slots(4), 1)
        self.assertIsNone(pq.next_in_line(led))
        late = self.board.add("DRE-LATE")
        claim_write(self.board, late, "run-late")
        led = self.board.ledger()
        mine = [r for r in led.running if r.card == late][0]
        self.assertEqual(pq.taken_before(led, mine), 3)

    def test_query_names_exactly_the_three_lanes(self):
        with mock.patch.object(linear_ops, "gql_paged", self.board.gql_paged):
            pq.read_board(linear_ops)
        query = self.board.queries[-1]
        self.assertEqual(pq.LEDGER_LANES, ("Planning", "In Progress", "Green Light"))
        for lane in pq.LEDGER_LANES:
            self.assertIn(f'"{lane}"', query)
        others = [n for n in lane_contract.load()["lanes"] if n not in pq.LEDGER_LANES]
        self.assertTrue(others)
        for lane in others:
            self.assertNotIn(f'"{lane}"', query, lane)
        self.assertIn(f"comments(first: {linear_ops.COMMENT_WINDOW})", query)
        self.assertIn("$after", query)

    def test_docstring_states_the_residual_window(self):
        doc = " ".join(pq.__doc__.split())
        self.assertIn("the one-off route moves its card to `Backlog`", doc)
        self.assertIn("the classification bounce moves a card to `Triage`", doc)
        self.assertIn("the run holds a slot the ledger cannot see", doc)
        self.assertIn("no model step runs in it", doc)


# --------------------------------------------------------------------------- #
# 13. the cap: one number, found beside the script, one reader                 #
# --------------------------------------------------------------------------- #


class TheCap(_Base):
    def test_committed_file_is_the_contract(self):
        with open(os.path.join(ROOT, "config", "planner-queue.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), COMMITTED)
        with mock.patch.dict(os.environ):
            os.environ.pop("PLANNER_QUEUE_CONFIG", None)
            self.assertEqual(pq.cap(), 2)
            self.assertEqual(pq.waiting_max(), 360)

    def test_check_finds_the_file_relative_to_the_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            bp = os.path.join(tmp, "product", ".bureau-pipeline")
            os.makedirs(os.path.join(bp, "scripts"))
            shutil.copy(os.path.join(SCRIPTS, "planner_queue.py"),
                        os.path.join(bp, "scripts", "planner_queue.py"))
            shutil.copytree(os.path.join(ROOT, "config"), os.path.join(bp, "config"))
            # The product repo's own config/ must never be the one read.
            os.makedirs(os.path.join(tmp, "product", "config"))
            with open(os.path.join(tmp, "product", "config", "planner-queue.json"), "w") as f:
                f.write("not json")
            elsewhere = os.path.join(tmp, "elsewhere")
            os.makedirs(elsewhere)
            env = {k: v for k, v in os.environ.items() if k != "PLANNER_QUEUE_CONFIG"}
            script = os.path.join(bp, "scripts", "planner_queue.py")
            runs = ((elsewhere, [sys.executable, script, "check"]),
                    (os.path.join(tmp, "product"),
                     [sys.executable, ".bureau-pipeline/scripts/planner_queue.py", "check"]))
            for cwd, argv in runs:
                p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn(os.path.join(".bureau-pipeline", "config", "planner-queue.json"),
                              p.stdout)

    def test_env_seam_sets_the_cap(self):
        with config_file(dict(COMMITTED, max_running=2)):
            self.assertEqual(pq.cap(), 2)
            idents = [self.board.add(f"DRE-{9200 + i}") for i in range(19)]
            for ident in idents:
                claim_write(self.board, ident, f"run-{ident}")
            outs = [claim_read(self.board, i, f"run-{i}") for i in idents]
            self.assertEqual(sum(o["admitted"] == "true" for o in outs), 2)

    def _readers(self, word):
        p = subprocess.run(
            ["grep", "-rlI", word, "scripts/", ".github/workflows/", "tests/"],
            cwd=ROOT, capture_output=True, text=True)
        return {os.path.normpath(x) for x in p.stdout.split()}

    def test_one_reader_of_max_running(self):
        self.assertEqual(self._readers("max_running"),
                         {"scripts/planner_queue.py", "tests/test_planner_queue.py"})

    def test_one_reader_of_waiting_max_minutes(self):
        self.assertEqual(self._readers("waiting_max_minutes"),
                         {"scripts/planner_queue.py", "tests/test_planner_queue.py"})


# --------------------------------------------------------------------------- #
# 14. a bad config file                                                        #
# --------------------------------------------------------------------------- #


BAD_CONFIGS = {
    "missing": None,
    "unparsable": "{not json",
    "missing key": json.dumps({k: v for k, v in COMMITTED.items() if k != "max_running"}),
    "zero": json.dumps(dict(COMMITTED, max_running=0)),
    "non-integer": json.dumps(dict(COMMITTED, max_running="4")),
    "a float": json.dumps(dict(COMMITTED, max_running=4.5)),
    "a boolean": json.dumps(dict(COMMITTED, max_running=True)),
}


@contextlib.contextmanager
def bad_config(kind):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "planner-queue.json")
        if BAD_CONFIGS[kind] is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(BAD_CONFIGS[kind])
        with mock.patch.dict(os.environ, {"PLANNER_QUEUE_CONFIG": path}):
            yield path


class BadConfig(_Base):
    def test_load_raises_naming_the_path(self):
        for kind in BAD_CONFIGS:
            with self.subTest(kind=kind), bad_config(kind) as path:
                with self.assertRaises(pq.PlannerQueueError) as ctx:
                    pq.load()
                self.assertIn(path, str(ctx.exception))
                with self.assertRaises(pq.PlannerQueueError):
                    pq.cap()

    def test_path_argument_wins(self):
        with bad_config("zero"):
            self.assertEqual(pq.load(os.path.join(ROOT, "config", "planner-queue.json")),
                             COMMITTED)

    def test_check_exits_2_with_the_reason(self):
        for kind in BAD_CONFIGS:
            with self.subTest(kind=kind), bad_config(kind) as path:
                rc, _, _, err = run_cli(["check"])
                self.assertEqual(rc, 2)
                self.assertIn(path, err)
        rc, _, out, _ = run_cli(["check"])
        self.assertEqual(rc, 0, out)
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "planner_queue.py"),
                            "check"], capture_output=True, text=True,
                           env={k: v for k, v in os.environ.items()
                                if k != "PLANNER_QUEUE_CONFIG"})
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_claim_fails_open_on_a_bad_config(self):
        card = self.board.add("DRE-BAD")
        with bad_config("unparsable") as path:
            rc, out, stdout, _ = cli_claim(card, "run-bad")
        self.assertEqual(rc, 0)
        self.assertEqual(out["admitted"], "true")
        self.assertIn("::warning::", stdout)
        self.assertIn(path, stdout)
        # The claim itself was posted first, so the ledger still sees the run.
        self.assertEqual(self.board.newest(card).state, "claimed")


# --------------------------------------------------------------------------- #
# 15. the TTL outlasts the plan job                                            #
# --------------------------------------------------------------------------- #


class Bounds(_Base):
    def test_ttl_covers_the_plan_timeout_and_the_wait_covers_the_ttl(self):
        with open(os.path.join(ROOT, ".github", "workflows", "plan.yml"),
                  encoding="utf-8") as f:
            timeout = yaml.safe_load(f)["jobs"]["plan"]["timeout-minutes"]
        # The COMMITTED file, not `_Base`'s pinned one: that pins the TTL the
        # ledger's lifetimes are written against (DRE-5288), and this is the
        # number a live claim actually gets.
        cfg = pq.load(pq.CONFIG_PATH)
        self.assertGreaterEqual(cfg["claim_ttl_minutes"], timeout)
        self.assertGreater(cfg["waiting_max_minutes"], cfg["claim_ttl_minutes"])


# --------------------------------------------------------------------------- #
# 16. waited_minutes / overdue                                                 #
# --------------------------------------------------------------------------- #


class WaitClock(_Base):
    def test_minutes_since_line_entry(self):
        card = self.board.add("DRE-WC")
        self.board.seed(card, "waiting", T0)
        nodes = self.board.nodes(card)
        self.assertEqual(pq.waited_minutes(nodes, now=T0 + timedelta(minutes=42)), 42.0)
        self.assertTrue(pq.overdue(nodes, now=T0 + timedelta(minutes=361)))
        self.assertFalse(pq.overdue(nodes, now=T0 + timedelta(minutes=359)))

    def test_none_for_a_card_not_in_line(self):
        card = self.board.add("DRE-NL")
        self.assertIsNone(pq.waited_minutes([], now=T0))
        self.board.seed(card, "claimed", T0, run="r")
        self.assertIsNone(pq.waited_minutes(self.board.nodes(card),
                                            now=T0 + timedelta(minutes=5)))
        self.assertFalse(pq.overdue(self.board.nodes(card), now=T0 + timedelta(minutes=5)))

    def test_redispatched_card_keeps_its_age(self):
        card = self.board.add("DRE-RW")
        self.board.seed(card, "waiting", T0)
        self.board.seed(card, "dispatched", T0 + timedelta(minutes=300), run="sweep")
        self.board.seed(card, "claimed", T0 + timedelta(minutes=301), run="r2")
        self.board.seed(card, "waiting", T0 + timedelta(minutes=302), run="r2")
        nodes = self.board.nodes(card)
        now = T0 + timedelta(minutes=361)
        self.assertEqual(pq.waited_minutes(nodes, now=now), 361.0)
        self.assertTrue(pq.overdue(nodes, now=now))


# --------------------------------------------------------------------------- #
# 17. TTL, grace, in_line                                                      #
# --------------------------------------------------------------------------- #


class Lifetimes(_Base):
    def test_expired_claim_counts_as_released(self):
        card = self.board.add("DRE-TTL")
        self.board.seed(card, "claimed", T0, run="r1")
        nodes = self.board.nodes(card)
        self.assertEqual(pq.state(nodes, now=T0 + timedelta(minutes=104)), "claimed")
        self.assertEqual(pq.state(nodes, now=T0 + timedelta(minutes=106)), "released")
        self.assertIsNone(pq.open_claim(nodes, now=T0 + timedelta(minutes=106)))
        # ...for the line entry too: an old wait before the dead claim is
        # not this card's place in line.
        card2 = self.board.add("DRE-TTL2")
        self.board.seed(card2, "waiting", T0)
        self.board.seed(card2, "claimed", T0 + timedelta(minutes=1), run="r1")
        self.board.seed(card2, "waiting", T0 + timedelta(minutes=200), run="r2")
        self.assertEqual(pq.line_entry(self.board.nodes(card2),
                                       now=T0 + timedelta(minutes=201)),
                         iso(T0 + timedelta(minutes=200)))

    def test_a_refused_claim_past_the_ttl_does_not_reset_the_line(self):
        card = self.board.add("DRE-OLDWAIT")
        self.board.seed(card, "waiting", T0)
        self.board.seed(card, "claimed", T0 + timedelta(minutes=1), run="r1")
        self.board.seed(card, "waiting", T0 + timedelta(minutes=2), run="r1")
        self.assertEqual(pq.line_entry(self.board.nodes(card),
                                       now=T0 + timedelta(minutes=200)), iso(T0))

    def test_lost_dispatch_waits_again(self):
        card = self.board.add("DRE-LOST")
        self.board.seed(card, "waiting", T0)
        self.board.seed(card, "dispatched", T0 + timedelta(minutes=1), run="sweep")
        nodes = self.board.nodes(card)
        self.assertEqual(pq.state(nodes, now=T0 + timedelta(minutes=5)), "dispatched")
        self.assertTrue(pq.in_line(nodes, now=T0 + timedelta(minutes=5)))
        self.assertEqual(pq.state(nodes, now=T0 + timedelta(minutes=12)), "waiting")
        self.assertTrue(pq.in_line(nodes, now=T0 + timedelta(minutes=12)))

    def test_in_line(self):
        for state, expected in (("waiting", True), ("claimed", False),
                                ("released", False)):
            with self.subTest(state=state):
                card = self.board.add(f"DRE-IL-{state}")
                self.board.seed(card, state, T0, run="r1")
                self.assertEqual(pq.in_line(self.board.nodes(card),
                                            now=T0 + timedelta(minutes=1)), expected)

    def test_a_release_by_a_run_holding_nothing_does_not_drop_a_waiter(self):
        card = self.board.add("DRE-NOOP")
        self.board.seed(card, "claimed", T0, run="r1")
        self.board.seed(card, "waiting", T0 + timedelta(seconds=1), run="r1")
        self.board.seed(card, "released", T0 + timedelta(seconds=2), run="r1",
                        because="finished")
        nodes = self.board.nodes(card)
        self.assertEqual(pq.state(nodes, now=T0 + timedelta(minutes=1)), "waiting")
        self.assertEqual(pq.line_entry(nodes, now=T0 + timedelta(minutes=1)),
                         iso(T0 + timedelta(seconds=1)))


# --------------------------------------------------------------------------- #
# 18. the CLI's failure paths                                                  #
# --------------------------------------------------------------------------- #


class FailurePaths(_Base):
    def test_claim_with_an_unreadable_board(self):
        card = self.board.add("DRE-UR")

        def boom(*a, **k):
            raise linear_ops.LinearError("linear is down")

        with mock.patch.object(linear_ops, "gql_paged", boom):
            rc, out, stdout, _ = cli_claim(card, "run-ur")
        self.assertEqual(rc, 0)
        self.assertEqual(out["admitted"], "true")
        self.assertIn("::warning::", stdout)

    def test_claim_with_a_failed_write(self):
        card = self.board.add("DRE-FW")

        def boom(*a, **k):
            raise linear_ops.LinearError("write refused")

        with mock.patch.object(linear_ops, "cmd_comment", boom):
            rc, out, stdout, _ = cli_claim(card, "run-fw")
        self.assertEqual((rc, out["admitted"]), (0, "true"))
        self.assertIn("::warning::", stdout)

    def test_dispatch_passes_the_record(self):
        card = self.board.add("DRE-DR")
        calls = []
        with mock.patch.object(plan_run, "fire",
                               lambda c, r, **k: (calls.append((c, r, k)) or (True, ""))):
            rc, *_ = run_cli(["dispatch", card, "--run-id", "run-d", "--repo", REPO,
                              "--trigger-state", "in progress"])
        self.assertEqual(rc, 0)
        record, repo, kwargs = calls[0]
        self.assertIsInstance(record, dict)
        self.assertEqual(record["identifier"], card)
        self.assertEqual(repo, REPO)
        self.assertEqual(kwargs["trigger_state"], "in progress")
        self.assertEqual(self.board.newest(card).state, "dispatched")

    def test_dispatch_failure_posts_nothing(self):
        card = self.board.add("DRE-DF")
        with mock.patch.object(plan_run, "fire", lambda c, r, **k: (False, "rc=1")):
            rc, _, stdout, _ = run_cli(["dispatch", card, "--run-id", "run-d",
                                        "--repo", REPO, "--trigger-state", "Planning"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.board.receipts(card), [])
        self.assertIn("::warning::", stdout)

    def test_dispatch_with_an_unreadable_card(self):
        card = self.board.add("DRE-DU")

        def boom(*a, **k):
            raise linear_ops.LinearError("no card")

        fired = []
        with mock.patch.object(linear_ops, "gql", boom), \
                mock.patch.object(plan_run, "fire",
                                  lambda *a, **k: fired.append(a) or (True, "")):
            rc, _, stdout, _ = run_cli(["dispatch", card, "--run-id", "run-d",
                                        "--repo", REPO, "--trigger-state", "Planning"])
        self.assertEqual(rc, 0)
        self.assertEqual(fired, [])
        self.assertEqual(self.board.receipts(card), [])
        self.assertIn("::warning::", stdout)

    def test_release_and_next_never_fail(self):
        def boom(*a, **k):
            raise linear_ops.LinearError("down")

        with mock.patch.object(linear_ops, "cmd_comment", boom):
            rc, _, stdout, _ = run_cli(["release", "DRE-X", "--run-id", "r"])
        self.assertEqual(rc, 0)
        self.assertIn("::warning::", stdout)
        with mock.patch.object(linear_ops, "gql_paged", boom):
            rc, out, stdout, _ = run_cli(["next"])
        self.assertEqual(rc, 0)
        self.assertEqual(out["card"], "")
        self.assertIn("::warning::", stdout)

    def test_release_writes_the_receipt(self):
        card = self.board.add("DRE-REL")
        claim_write(self.board, card, "run-rel")
        rc, *_ = run_cli(["release", card, "--run-id", "run-rel", "--because", "finished"])
        self.assertEqual(rc, 0)
        newest = self.board.newest(card)
        self.assertEqual((newest.state, newest.run, newest.because),
                         ("released", "run-rel", "finished"))
        self.assertIsNone(pq.open_claim(self.board.nodes(card)))


# --------------------------------------------------------------------------- #
# 19. the act-receipt checks                                                   #
# --------------------------------------------------------------------------- #


class ActReceipts(unittest.TestCase):
    def test_one_posting_site_through_post(self):
        with open(os.path.join(SCRIPTS, "planner_queue.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertEqual(source.count("cmd_comment("), 1)
        import check_act_receipts  # noqa: PLC0415
        mine = [s for s in check_act_receipts.python_sites()
                if s.path == "scripts/planner_queue.py"]
        self.assertEqual(len(mine), 1)
        doc = json.load(open(os.path.join(ROOT, "config", "pipeline-acts.json"),
                             encoding="utf-8"))
        rows = [u for u in doc["unconverted"] if u["file"] == "scripts/planner_queue.py"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "not-an-act")
        self.assertFalse(any("planner-slot" in json.dumps(a) for a in doc["acts"]))

    def test_checks_are_green(self):
        for argv in (["scripts/check_act_receipts.py"], ["scripts/pipeline_act.py", "check"]):
            p = subprocess.run([sys.executable, *argv], cwd=ROOT,
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


# --------------------------------------------------------------------------- #
# 20. the docstring                                                            #
# --------------------------------------------------------------------------- #


class Docstring(unittest.TestCase):
    def test_boundary_answers_and_the_dre5152_reading(self):
        doc = " ".join(pq.__doc__.split())
        for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            self.assertIn(q, doc)
        self.assertIn("quota", doc)
        self.assertIn("crash between the claim write and the ledger read", doc)
        self.assertIn("DRE-5152", doc)
        self.assertIn("lands first", doc)


# =========================================================================== #
# DRE-5180 — plan.yml's end-of-run steps, driven through the same CLI calls    #
# =========================================================================== #
#
# `plan.yml` ends an admitted run with `release <CARD> --run-id <id> --because
# finished`, then `next --github-output`, then — when `next` named a card and
# the owner's token minted — `dispatch <NEXT_CARD> … --reason <NEXT_REASON>`.
# These walks run exactly those three commands against the fake board, and
# count `Ledger.running` after every step.


def end_release(card: str, run: str) -> None:
    """plan.yml's `Planner slot — release` step."""
    rc, *_ = run_cli(["release", card, "--run-id", run, "--because", "finished"])
    assert rc == 0


def end_next() -> dict:
    """plan.yml's `Planner slot — next in line` step: its step outputs."""
    rc, out, *_ = run_cli(["next"])
    assert rc == 0
    return out


def end_dispatch(out: dict, run: str) -> list:
    """plan.yml's `Planner slot — start the next card` step, fed `next`'s
    outputs exactly as its `NEXT_*` env carries them. Returns the fires."""
    fired = []
    with mock.patch.object(plan_run, "fire",
                           lambda c, r, **k: (fired.append((c, r, k)) or (True, ""))):
        rc, *_ = run_cli(["dispatch", out["card"], "--run-id", run,
                          "--repo", out["repo"], "--trigger-state", out["trigger_state"],
                          "--reason", out["reason"]])
    assert rc == 0
    return fired


class _EndOfRun(_Base):
    def cap_held(self) -> None:
        self.assertLessEqual(len(self.board.ledger().running), 4)


# --------------------------------------------------------------------------- #
# 21. a run's end frees its slot for the head of the line                      #
# --------------------------------------------------------------------------- #


class EndOfRunRelease(_EndOfRun):
    def _fixture(self):
        running = fill_running(self.board, 4)
        w1 = self.board.add("DRE-W1")
        self.board.seed(w1, "waiting", self.board.clock, place=1, of=2)
        self.board.tick(60)
        w2 = self.board.add("DRE-W2")
        self.board.seed(w2, "waiting", self.board.clock, place=2, of=2)
        self.board.tick(60)
        led = self.board.ledger()
        self.assertEqual((led.free_slots(4), pq.next_in_line(led)), (0, None))
        return running, w1, w2

    def _end(self, how: str, card: str) -> None:
        if how == "run-gone":
            # A death the sweep reported: the sweep's own release.
            pq.post_released(linear_ops, card, run_id=f"run-{card}", repo=REPO,
                             trigger_state="Planning", because="run-gone")
        else:
            # Success, or a failed planner step: the job's own `always()`
            # release posts the same receipt either way.
            end_release(card, f"run-{card}")

    def test_success_failure_and_run_gone_each_free_one_slot_for_the_head(self):
        for how in ("success", "failure", "run-gone"):
            with self.subTest(how=how):
                self.board = Board()
                with self.board.live():
                    running, w1, w2 = self._fixture()
                    self._end(how, running[0])
                    newest = self.board.newest(running[0])
                    self.assertEqual((newest.state, newest.because),
                                     ("released", "run-gone" if how == "run-gone"
                                      else "finished"))
                    led = self.board.ledger()
                    self.assertEqual(pq.next_in_line(led).card, w1)
                    self.assertEqual(led.free_slots(4), 1)
                    out = end_next()
                    self.assertEqual((out["card"], out["repo"]), (w1, REPO))
                    self.assertEqual(f'{out["owner"]}/{out["name"]}', REPO)
                    fired = end_dispatch(out, "run-finisher")
                    self.assertEqual(len(fired), 1)
                    self.assertEqual(self.board.newest(w1).state, "dispatched")
                    # The dispatched card holds the slot inside the grace.
                    led = self.board.ledger()
                    self.assertEqual(led.free_slots(4), 0)
                    self.assertIsNone(pq.next_in_line(led))
                    self.assertEqual(end_next()["card"], "")
                    self.assertEqual([r.card for r in led.reserved], [w1])
                    self.cap_held()

    def test_two_slots_free_serve_the_first_then_the_second_never_the_first_twice(self):
        running, w1, w2 = self._fixture()
        end_release(running[0], f"run-{running[0]}")
        end_release(running[1], f"run-{running[1]}")
        self.assertEqual(self.board.ledger().free_slots(4), 2)
        first = end_next()
        self.assertEqual(first["card"], w1)
        end_dispatch(first, "run-a")
        second = end_next()
        self.assertEqual(second["card"], w2)
        end_dispatch(second, "run-b")
        led = self.board.ledger()
        self.assertEqual(sorted(r.card for r in led.reserved), sorted([w1, w2]))
        self.assertEqual((led.free_slots(4), pq.next_in_line(led)), (0, None))
        self.cap_held()


# --------------------------------------------------------------------------- #
# 22. the handover through the end-of-run steps, both orders                   #
# --------------------------------------------------------------------------- #


class EndOfRunHandover(_EndOfRun):
    def _sender_holds_the_fourth_slot(self, lane: str = "In Progress"):
        others = fill_running(self.board, 3)
        card = self.board.add("DRE-EP", lane=lane)
        self.board.seed(card, "claimed", self.board.clock, run="run-S",
                        trigger="in progress")
        self.board.tick(60)
        self.assertEqual(len(self.board.ledger().running), 4)
        return others, card

    def test_i_the_review_claims_before_its_sender_releases(self):
        for lane in ("In Progress", "Green Light"):
            with self.subTest(lane=lane):
                self.board = Board()
                with self.board.live():
                    self._walk_i(lane)

    def _walk_i(self, lane: str) -> None:
        others, card = self._sender_holds_the_fourth_slot(lane)
        waiter = self.board.add("DRE-WAIT")
        self.board.seed(waiter, "waiting", self.board.clock)
        self.board.tick()
        # S's `review_rerun.py dispatch` (re-review) fires first; the run it
        # asked for, C, claims with S as its sender.
        rc, out, *_ = cli_claim(card, "run-C", "in progress",
                                "--sent-by-run", "run-S", "--reason", "re-review")
        self.assertEqual((rc, out["admitted"], out["inherited"]), (0, "true", "true"))
        self.cap_held()
        # S reaches its end: release, then next.
        end_release(card, "run-S")
        led = self.board.ledger()
        self.assertEqual([r.card for r in led.running].count(card), 1)
        self.assertEqual(led.free_slots(4), 0)
        self.assertIsNone(pq.next_in_line(led))
        self.assertEqual(end_next()["card"], "", "S's next must name nobody")
        self.cap_held()
        # C's own release frees the slot for the head of the line.
        end_release(card, "run-C")
        led = self.board.ledger()
        self.assertNotIn(card, [r.card for r in led.running])
        self.assertEqual(pq.next_in_line(led).card, waiter)
        self.assertEqual(end_next()["card"], waiter)
        self.cap_held()

    def test_ii_the_sender_releases_and_dispatches_before_the_review_claims(self):
        others, card = self._sender_holds_the_fourth_slot()
        x = self.board.add("DRE-X")
        self.board.seed(x, "waiting", self.board.clock)
        self.board.tick(60)
        y = self.board.add("DRE-Y")
        self.board.seed(y, "waiting", self.board.clock)
        self.board.tick(60)
        # S ends first: its release, its next (X), its dispatch of X.
        end_release(card, "run-S")
        self.cap_held()
        out = end_next()
        self.assertEqual(out["card"], x)
        end_dispatch(out, "run-S")
        led = self.board.ledger()
        self.assertEqual([r.card for r in led.reserved], [x])
        self.cap_held()
        # C arrives and finds S's claim closed: 3 running + X reserved.
        rc, out, *_ = cli_claim(card, "run-C", "in progress",
                                "--sent-by-run", "run-S", "--reason", "re-review")
        self.assertEqual((rc, out["admitted"], out["inherited"]), (0, "false", "false"))
        self.assertEqual(out["place"], "1")
        newest = self.board.newest(card)
        self.assertEqual((newest.state, newest.reason, newest.trigger),
                         ("waiting", "re-review", "in progress"))
        self.assertIn("waiting for a planner: place 1 of ", self.board.nodes(card)[0]["body"])
        self.cap_held()
        # X claims on its reservation; then one running card finishes.
        rc, out, *_ = cli_claim(x, "run-x")
        self.assertEqual(out["admitted"], "true")
        self.assertEqual(len(self.board.ledger().running), 4)
        end_release(others[0], f"run-{others[0]}")
        nxt = pq.next_in_line(self.board.ledger())
        self.assertEqual((nxt.card, nxt.reason), (card, "re-review"))
        out = end_next()
        self.assertEqual((out["card"], out["reason"], out["trigger_state"]),
                         (card, "re-review", "in progress"))
        self.cap_held()


# --------------------------------------------------------------------------- #
# 23. the double-finish race: two different cards for one slot                 #
# --------------------------------------------------------------------------- #


class DoubleFinishRace(_EndOfRun):
    """One free slot; the finishing run dispatches X and the sweep dispatches
    Y. Neither is a fifth planner and neither card loses its place — which of
    the two is admitted is the foundation card's claim-order rule, over every
    receipt on the board when each claim reads."""

    def _fixture(self):
        fill_running(self.board, 3)
        x, y, z = (self.board.add(i) for i in ("DRE-X", "DRE-Y", "DRE-Z"))
        entries = {}
        for ident in (x, y, z):
            entries[ident] = self.board.seed(ident, "waiting", self.board.clock)["createdAt"]
            self.board.tick(60)
        self.assertEqual(self.board.ledger().free_slots(4), 1)
        return x, y, z, entries

    def _dispatch(self, ident: str, run: str) -> None:
        end_dispatch({"card": ident, "repo": REPO, "trigger_state": "Planning",
                      "reason": ""}, run)
        self.cap_held()

    def _claim(self, ident: str) -> str:
        rc, out, *_ = cli_claim(ident, f"run-{ident}")
        self.assertEqual(rc, 0)
        self.cap_held()
        return out["admitted"]

    def test_x_claims_first_and_is_admitted_y_waits_at_its_original_entry(self):
        x, y, z, entries = self._fixture()
        self._dispatch(x, "run-finisher")
        self.assertEqual(self._claim(x), "true")
        self._dispatch(y, "sweep")
        self.assertEqual(self._claim(y), "false")
        led = self.board.ledger()
        self.assertEqual(len(led.running), 4)
        self.assertEqual(led.waiting[0].card, y)
        self.assertEqual([w.card for w in led.waiting], [y, z])
        self.assertEqual(pq.line_entry(self.board.nodes(y), now=self.board.clock),
                         entries[y])

    def test_both_claims_written_before_either_reads_admit_the_earlier(self):
        for reads in (("X", "Y"), ("Y", "X")):
            with self.subTest(reads=reads):
                self.board = Board()
                with self.board.live():
                    x, y, z, entries = self._fixture()
                    self._dispatch(x, "run-finisher")
                    self._dispatch(y, "sweep")
                    claim_write(self.board, x, f"run-{x}")
                    claim_write(self.board, y, f"run-{y}")
                    out = {}
                    for which in reads:
                        ident = x if which == "X" else y
                        out[ident] = claim_read(self.board, ident, f"run-{ident}")
                    self.assertEqual((out[x]["admitted"], out[y]["admitted"]),
                                     ("true", "false"))
                    led = self.board.ledger()
                    self.assertEqual(len(led.running), 4)
                    self.assertEqual(led.waiting[0].card, y)
                    self.assertEqual(pq.line_entry(self.board.nodes(y),
                                                   now=self.board.clock), entries[y])

    def test_both_dispatches_land_before_either_claim_one_is_admitted(self):
        """Both reservations count against the first claim to read, so here the
        FIRST claimant waits and the second is admitted — at its original line
        entry, at the head of the line. Still one slot, one run, no card sent
        to the back."""
        x, y, z, entries = self._fixture()
        self._dispatch(x, "run-finisher")
        self._dispatch(y, "sweep")
        admitted = {x: self._claim(x), y: self._claim(y)}
        self.assertEqual(sorted(admitted.values()), ["false", "true"])
        led = self.board.ledger()
        self.assertEqual(len(led.running), 4)
        waiter = x if admitted[x] == "false" else y
        self.assertEqual(led.waiting[0].card, waiter)
        self.assertEqual(pq.line_entry(self.board.nodes(waiter), now=self.board.clock),
                         entries[waiter])
        self.assertEqual([w.card for w in led.waiting], [waiter, z])


if __name__ == "__main__":
    unittest.main()
