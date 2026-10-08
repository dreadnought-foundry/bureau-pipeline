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
 13b. PLANNER_MAX_RUNNING: the number in force, bounded by the file's ceiling
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
COMMITTED = {"max_running": 3, "claim_ttl_minutes": 115,
             "dispatched_grace_minutes": 10, "waiting_max_minutes": 360,
             "max_running_ceiling": 4,
             "ceiling_reason": (
                 "The planners spend their own Linear bucket of 5,000 requests an "
                 "hour (DRE-5589). The only per-slot measurement is from 2026-09-30, "
                 "when four planner and critic steps together spent about 185 to 260 "
                 "requests a minute on the old shared key; four slots at that rate "
                 "would empty 5,000 in 19 to 27 minutes if sustained. Until the "
                 "bucket has been measured with four planners running (the proof of "
                 "DRE-5783), nothing above four is allowed.")}

#: The ledger's behavior is tested at the four slots it was written against
#: (DRE-5176). The committed number is `TheCap`'s contract alone: DRE-5326
#: dropped it to two on 2026-09-30, when four planners out-spent Linear's
#: refill, and DRE-5634 raised it to three on 2026-10-02, once the planners
#: spent their own OAuth bucket (DRE-5589). The rules below do not change with
#: the number. The same for the
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
        # The console's published number (DRE-5792) is unset unless a test
        # sets it: a runner's environment never decides a test's cap.
        os.environ.pop("PLANNER_MAX_RUNNING", None)
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
            # The release's dispatch lands, then the run it started claims.
            pq.post_dispatched(linear_ops, idents[2], run_id=f"run-{first}", repo=REPO,
                               trigger_state="Planning")
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

    def test_fresh_arrival_one_second_earlier_waits_behind_the_line(self):
        """DRE-6329 turned this round: the fresh claim is earlier in claim
        order than A's, but B and C were already waiting, so it does not take
        the slot. A, dispatched into it, is admitted; the fresh card joins the
        line behind B and C."""
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
        out = claim_read(self.board, fresh, "run-fresh")
        self.assertEqual((out["admitted"], out["place"], out["waiting"]),
                         ("false", "3", "3"))
        self.assertEqual(claim_read(self.board, a, "run-a")["admitted"], "true")
        led = self.board.ledger()
        self.assertEqual([w.card for w in led.waiting], [b, c, fresh])
        self.assertIn(a, [r.card for r in led.running])
        self.assertIsNone(pq.next_in_line(led))
        pq.post_released(linear_ops, "RUN-2", run_id="run-RUN-2", repo=REPO,
                         trigger_state="Planning", because="finished")
        self.assertEqual(pq.next_in_line(self.board.ledger()).card, b)


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
        self.assertEqual(kwargs.pop("event"), plan_run.PLAN_EVENT)
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
        self.assertEqual(kwargs.pop("event"), plan_run.PLAN_EVENT)
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
            self.assertEqual(pq.cap(), 3)
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
        # hygiene-done-records holds byte-pinned copies of proof records
        # (tests/test_hygiene_done.py) — prose that quotes the cap, not a reader.
        p = subprocess.run(
            ["grep", "-rlI", "--exclude-dir=hygiene-done-records", word,
             "scripts/", ".github/workflows/", "tests/"],
            cwd=ROOT, capture_output=True, text=True)
        return {os.path.normpath(x) for x in p.stdout.split()}

    def test_one_reader_of_max_running(self):
        self.assertEqual(self._readers("max_running"),
                         {"scripts/planner_queue.py", "tests/test_planner_queue.py"})

    def test_one_reader_of_waiting_max_minutes(self):
        self.assertEqual(self._readers("waiting_max_minutes"),
                         {"scripts/planner_queue.py", "tests/test_planner_queue.py"})


# --------------------------------------------------------------------------- #
# 13b. PLANNER_MAX_RUNNING — the console's number, under the file's ceiling    #
# --------------------------------------------------------------------------- #


def captured(fn, *args, **kwargs):
    """`fn(*args, **kwargs)` and what it printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        value = fn(*args, **kwargs)
    return value, out.getvalue()


class PublishedCap(_Base):
    """DRE-5792: the console publishes `PLANNER_MAX_RUNNING` to each repo, and
    `cap()` is the one place the number in force is decided — for admission
    through `claim` as much as for the sweep's `next_in_line`."""

    def setUp(self):
        super().setUp()
        committed = config_file(COMMITTED)
        committed.__enter__()
        self.addCleanup(committed.__exit__, None, None, None)

    def published(self, value):
        return mock.patch.dict(os.environ, {"PLANNER_MAX_RUNNING": value})

    def test_a_positive_integer_is_the_cap(self):
        with self.published("4"):
            self.assertEqual(captured(pq.cap)[0], 4)
        with self.published("2"):
            self.assertEqual(captured(pq.cap)[0], 2)

    def test_above_the_ceiling_is_clamped_with_a_warning(self):
        with self.published("9"):
            value, out = captured(pq.cap)
        self.assertEqual(value, 4)
        warnings = [line for line in out.splitlines() if line.startswith("::warning::")]
        self.assertEqual(len(warnings), 1, out)
        self.assertIn("9", warnings[0])
        self.assertIn("max_running_ceiling=4", warnings[0])

    def test_anything_else_falls_back_to_the_file_with_a_warning(self):
        for bad in ("abc", "0", "4.5", "true", "-4"):
            with self.subTest(value=bad), self.published(bad):
                value, out = captured(pq.cap)
                self.assertEqual(value, 3)
                warnings = [line for line in out.splitlines()
                            if line.startswith("::warning::")]
                self.assertEqual(len(warnings), 1, out)
                self.assertIn(f"'{bad}'", warnings[0])
                self.assertIn("max_running=3 is in force", warnings[0])
                self.assertNotIn("::notice::", out)

    def test_unset_or_empty_reads_the_file_in_silence(self):
        value, out = captured(pq.cap)
        self.assertEqual((value, out), (3, ""))
        with self.published(""):
            value, out = captured(pq.cap)
        self.assertEqual((value, out), (3, ""))

    def test_a_published_number_unlike_the_file_says_so(self):
        with self.published("4"):
            _, out = captured(pq.cap)
        self.assertIn("::notice::PLANNER_MAX_RUNNING=4 is in force on this repo; "
                      "config/planner-queue.json max_running=3 is not read here", out)
        self.assertNotIn("::warning::", out)

    def test_a_published_number_like_the_file_is_silent(self):
        with self.published("3"):
            value, out = captured(pq.cap)
        self.assertEqual((value, out), (3, ""))

    def test_cap_takes_an_already_loaded_config(self):
        cfg = dict(COMMITTED, max_running=2)
        with mock.patch.object(pq, "load", side_effect=AssertionError("loaded twice")):
            self.assertEqual(captured(pq.cap, cfg)[0], 2)
            with self.published("4"):
                self.assertEqual(captured(pq.cap, cfg)[0], 4)

    def test_claim_admits_by_the_published_number(self):
        """The card's reason for being: `settle_claim` used to read the file's
        number itself, so a published 4 changed who was dispatched next and
        never who was admitted."""
        idents = [self.board.add(f"DRE-{9700 + i}") for i in range(5)]
        with self.published("4"):
            for ident in idents:
                claim_write(self.board, ident, f"run-{ident}")
            outs, _ = captured(lambda: [claim_read(self.board, i, f"run-{i}")
                                        for i in idents])
        self.assertEqual([o["admitted"] for o in outs], ["true"] * 4 + ["false"])
        self.assertEqual((outs[-1]["place"], outs[-1]["waiting"]), ("1", "1"))
        newest = self.board.newest(idents[-1])
        self.assertEqual((newest.state, newest.place, newest.of), ("waiting", 1, 1))

    def test_claim_through_the_cli_admits_by_the_published_number(self):
        idents = [self.board.add(f"DRE-{9710 + i}") for i in range(5)]
        with self.published("4"):
            outs = [cli_claim(i, f"run-{i}")[1] for i in idents]
        self.assertEqual([o["admitted"] for o in outs], ["true"] * 4 + ["false"])
        self.assertEqual((outs[-1]["place"], outs[-1]["waiting"]), ("1", "1"))

    def test_the_ceiling_bounds_admission(self):
        idents = [self.board.add(f"DRE-{9720 + i}") for i in range(6)]
        with self.published("9"):
            for ident in idents:
                claim_write(self.board, ident, f"run-{ident}")
            outs, _ = captured(lambda: [claim_read(self.board, i, f"run-{i}")
                                        for i in idents])
        self.assertEqual(sum(o["admitted"] == "true" for o in outs), 4)

    def test_next_in_line_serves_a_fourth_card(self):
        fill_running(self.board, 3)
        waiter = self.board.add("DRE-9730")
        self.board.seed(waiter, "waiting", self.board.clock, run="run-waiter")
        self.board.tick(60)
        self.assertIsNone(captured(pq.next_in_line, self.board.ledger())[0])
        with self.published("4"):
            found, _ = captured(pq.next_in_line, self.board.ledger())
        self.assertEqual(found.card, waiter)

    def test_the_file_number_is_read_once_inside_cap(self):
        path = os.path.join(SCRIPTS, "planner_queue.py")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        self.assertEqual(len(re.findall(r'\["max_running"\]', source)), 1)
        line = source[:source.index('["max_running"]')].count("\n") + 1
        import ast  # noqa: PLC0415
        fn = next(node for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.FunctionDef) and node.name == "cap")
        self.assertTrue(fn.lineno <= line <= fn.end_lineno, line)

    def test_ceiling_and_reason_have_one_reader_each(self):
        self.assertEqual(pq.ceiling(), 4)
        self.assertEqual(pq.ceiling_reason(), COMMITTED["ceiling_reason"])
        self.assertEqual(pq.ceiling(dict(COMMITTED, max_running_ceiling=6)), 6)

    def test_check_names_the_number_in_force_and_its_source(self):
        rc, _, out, _ = run_cli(["check"])
        self.assertEqual(rc, 0)
        self.assertIn("max_running=3 max_running_ceiling=4 in force=3 (the file)", out)
        with self.published("4"):
            rc, _, out, _ = run_cli(["check"])
        self.assertEqual(rc, 0)
        self.assertIn("max_running=3 max_running_ceiling=4 in force=4 "
                      "(PLANNER_MAX_RUNNING)", out)
        with self.published("abc"):
            rc, _, out, _ = run_cli(["check"])
        self.assertEqual(rc, 0)
        self.assertIn("in force=3 (the file)", out)

    def test_docstring_says_the_variable_wins_and_how_to_go_back(self):
        doc = " ".join(pq.__doc__.split())
        self.assertIn("the file's `max_running` is not the number on that repo", doc)
        self.assertIn("gh variable delete PLANNER_MAX_RUNNING -R <owner/repo>", doc)
        self.assertIn("The ceiling is never published", doc)
        self.assertNotIn("THE NUMBER IS THREE", doc)


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
    "ceiling below max_running": json.dumps(dict(COMMITTED, max_running_ceiling=2)),
    "missing ceiling": json.dumps({k: v for k, v in COMMITTED.items()
                                   if k != "max_running_ceiling"}),
    "ceiling not an integer": json.dumps(dict(COMMITTED, max_running_ceiling="4")),
    "missing reason": json.dumps({k: v for k, v in COMMITTED.items()
                                  if k != "ceiling_reason"}),
    "empty reason": json.dumps(dict(COMMITTED, ceiling_reason="")),
    "blank reason": json.dumps(dict(COMMITTED, ceiling_reason="   ")),
    "reason not a string": json.dumps(dict(COMMITTED, ceiling_reason=4)),
}

#: The defect `load()` names for each of the ceiling's bad files.
CEILING_DEFECTS = {
    "ceiling below max_running": "max_running_ceiling=2 is below max_running=3",
    "missing ceiling": "missing key 'max_running_ceiling'",
    "ceiling not an integer": "max_running_ceiling must be an integer",
    "missing reason": "missing key 'ceiling_reason'",
    "empty reason": "ceiling_reason must be a non-empty string",
    "blank reason": "ceiling_reason must be a non-empty string",
    "reason not a string": "ceiling_reason must be a non-empty string",
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
                if kind in CEILING_DEFECTS:
                    self.assertIn(CEILING_DEFECTS[kind], err)
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


# =========================================================================== #
# DRE-5378 — a retry keeps its place, and a park ends it                      #
# =========================================================================== #
#
# DRE-5213, 2026-09-30, read off the card itself. Its planner run 36726491495
# claimed at 14:05:58Z and was admitted. The first critic sent the plan back,
# the planner revised it, and the run released at 14:34:52Z `because
# finished`. The automatic retry was a GitHub RE-RUN of that run, and a re-run
# keeps its run id: it claimed at 14:35:55Z under the same 36726491495 and was
# told to wait at `place 24 of 24`, behind every card of the 07:05 PT groom
# drain — cards whose own claims came after 14:05:58Z. It waited six hours and
# the watchdog parked it in Green Light at 20:40Z.

DRE5213 = "DRE-5213"
DRE5213_RUN = "36726491495"


def sep30(hms: str) -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    return datetime(2026, 9, 30, h, m, s, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 24. a re-run of a run that released keeps the place its first claim had      #
# --------------------------------------------------------------------------- #


class RetryKeepsItsPlace(_EndOfRun):
    #: Cards of the groom drain that claimed AFTER DRE-5213's first claim and
    #: were refused, at the times their own receipts carry.
    JOINED = (("DRE-3622", "14:06:12"), ("DRE-4267", "14:06:23"),
              ("DRE-5311", "14:19:19"), ("DRE-5314", "14:26:43"))

    def _morning(self) -> dict:
        """DRE-5213's receipts, posted by the real commands in their order:
        claimed, released by the run that finished, claimed again by that
        run's re-run, waiting. Returns the retry's `claim` outputs."""
        self.board.clock = sep30("14:00:00")
        self.running = fill_running(self.board, 3)
        self.board.add(DRE5213)
        self.board.clock = sep30("14:05:58")
        rc, out, *_ = cli_claim(DRE5213, DRE5213_RUN)
        self.assertEqual((rc, out["admitted"]), (0, "true"))
        for ident, hms in self.JOINED:
            self.board.add(ident)
            self.board.clock = sep30(hms)
            rc, out, *_ = cli_claim(ident, f"run-{ident}")
            self.assertEqual((rc, out["admitted"]), (0, "false"), ident)
        # The run's end: its release, its next, its dispatch of the head.
        self.board.clock = sep30("14:34:52")
        end_release(DRE5213, DRE5213_RUN)
        head = end_next()
        self.assertEqual(head["card"], "DRE-3622")
        end_dispatch(head, DRE5213_RUN)
        # The automatic retry: a re-run, under the same run id.
        self.board.clock = sep30("14:35:55")
        rc, out, *_ = cli_claim(DRE5213, DRE5213_RUN)
        self.assertEqual(rc, 0)
        self.cap_held()
        return out

    def test_the_receipts_are_dre5213s(self):
        """The fixture is the card's own sequence, not a look-alike."""
        self._morning()
        seen = [(r.state, r.run, r.because)
                for r in reversed(self.board.receipts(DRE5213))]
        self.assertEqual(seen, [("claimed", DRE5213_RUN, None),
                                ("released", DRE5213_RUN, "finished"),
                                ("claimed", DRE5213_RUN, None),
                                ("waiting", DRE5213_RUN, None)])

    def test_the_retry_waits_at_the_place_its_first_claim_had(self):
        out = self._morning()
        self.assertEqual(out["admitted"], "false")
        self.assertEqual((out["place"], out["waiting"]), ("1", "4"))
        self.assertIn("waiting for a planner: place 1 of 4",
                      self.board.nodes(DRE5213)[0]["body"])
        led = self.board.ledger()
        self.assertEqual([w.card for w in led.waiting],
                         [DRE5213, "DRE-4267", "DRE-5311", "DRE-5314"])

    def test_later_cards_join_behind_it_and_next_in_line_serves_it_first(self):
        self._morning()
        # DRE-3622 takes the slot it was dispatched into.
        self.board.clock = sep30("14:36:30")
        self.assertEqual(cli_claim("DRE-3622", "run-DRE-3622-2")[1]["admitted"], "true")
        for ident, hms in (("DRE-5327", "15:00:00"), ("DRE-5358", "15:05:00")):
            self.board.add(ident)
            self.board.clock = sep30(hms)
            rc, out, *_ = cli_claim(ident, f"run-{ident}")
            self.assertEqual(out["admitted"], "false")
        # Then a slot frees.
        self.board.clock = sep30("15:10:00")
        end_release(self.running[0], f"run-{self.running[0]}")
        led = self.board.ledger()
        self.assertEqual(led.free_slots(4), 1)
        self.assertEqual(pq.next_in_line(led).card, DRE5213)
        out = end_next()
        self.assertEqual((out["card"], out["trigger_state"]), (DRE5213, "Planning"))
        fired = end_dispatch(out, "run-finisher")
        self.assertEqual([c["identifier"] for c, _, _ in fired], [DRE5213])
        self.assertEqual(self.board.newest(DRE5213).state, "dispatched")
        self.cap_held()

    def test_its_wait_is_measured_from_the_retrys_own_waiting(self):
        """The place is the first claim's; the clock the watchdog reads is not.
        The card held a slot from 14:05:58 to 14:34:52 and waited from the
        retry's `waiting` on."""
        self._morning()
        nodes = self.board.nodes(DRE5213)
        now = sep30("15:35:56")
        self.assertEqual(pq.line_entry(nodes, now=now), iso(sep30("14:35:56")))
        self.assertEqual(pq.waited_minutes(nodes, now=now), 60.0)

    def test_a_waiter_whose_claim_came_from_a_finished_run_is_still_served(self):
        """Its run id has a `released … because finished` and GitHub reports the
        run completed; neither makes it a dead claim, and nothing drops it."""
        self._morning()
        later = sep30("20:00:00")
        led = self.board.ledger(now=later)
        self.assertEqual(pq.state(self.board.nodes(DRE5213), now=later), "waiting")
        self.assertNotIn(DRE5213, [r.card for r in led.running])
        self.assertNotIn(DRE5213, [r.card for r in pq.expired_claims(led, later)])
        self.assertEqual(led.waiting[0].card, DRE5213)
        self.assertEqual(led.waiting[0].run, DRE5213_RUN)

    def test_a_new_run_after_a_release_is_a_fresh_arrival(self):
        """The guard on the rule: only the run that released may continue its
        claim. A different run after a release — a re-send, a new dispatch —
        joins at the back, exactly as before."""
        self._morning()
        other = self.board.add("DRE-FRESH")
        self.board.clock = sep30("14:40:00")
        self.board.seed(other, "claimed", sep30("14:00:30"), run="run-old")
        self.board.seed(other, "released", sep30("14:30:00"), run="run-old",
                        because="finished")
        rc, out, *_ = cli_claim(other, "run-new")
        self.assertEqual(out["admitted"], "false")
        self.assertEqual([w.card for w in self.board.ledger().waiting][-1], other)


# --------------------------------------------------------------------------- #
# 25. a park out of Planning ends the place in line                            #
# --------------------------------------------------------------------------- #


class ParkEndsThePlace(_Base):
    """The same card, the same afternoon (the operator's comment on
    DRE-5378). Waiting from 14:35:56Z (07:35 PT), parked by the watchdog at
    20:40:48Z (13:40 PT), re-sent at 20:56Z (14:00 PT). Without a release the
    re-sent card came back 6+ hours "waited" and the 21:26Z sweep parked it
    again — "DRE-5213 has waited 411 minutes for a planner slot"."""

    def _afternoon(self, park: bool = True) -> list:
        card = self.board.add(DRE5213)
        self.board.seed(card, "claimed", sep30("14:35:55"), run=DRE5213_RUN)
        self.board.seed(card, "waiting", sep30("14:35:56"), run=DRE5213_RUN,
                        place=24, of=24)
        if park:
            self.board.seed(card, "released", sep30("20:40:48"),
                            run="36774224458", because="parked")
        self.board.seed(card, "claimed", sep30("20:56:32"), run="36776079981")
        self.board.seed(card, "waiting", sep30("20:56:33"), run="36776079981",
                        place=3, of=4)
        return self.board.nodes(card)

    def test_a_resend_after_a_park_starts_a_fresh_wait(self):
        nodes = self._afternoon()
        now = sep30("21:26:42")
        self.assertEqual(pq.line_entry(nodes, now=now), iso(sep30("20:56:33")))
        self.assertLess(pq.waited_minutes(nodes, now=now), 31)
        self.assertFalse(pq.overdue(nodes, now=now))

    def test_the_fixture_is_overdue_without_the_park(self):
        """The guard against a vacuous fixture: the same card with no park
        release IS overdue at 21:26Z — the release is what resets it."""
        nodes = self._afternoon(park=False)
        self.assertTrue(pq.overdue(nodes, now=sep30("21:26:42")))

    def test_a_parked_card_is_out_of_line(self):
        card = self.board.add("DRE-PARK")
        self.board.seed(card, "waiting", sep30("14:35:56"), run="r1")
        self.board.seed(card, "released", sep30("20:40:48"), run="sweep",
                        because="parked")
        nodes = self.board.nodes(card)
        self.assertEqual(pq.state(nodes, now=sep30("20:41:00")), "released")
        self.assertFalse(pq.in_line(nodes, now=sep30("20:41:00")))
        self.assertIn("parked", pq.BECAUSE)


# =========================================================================== #
# DRE-6329 — a new arrival never takes a freed slot while cards wait In line  #
# =========================================================================== #
#
# DRE-5810's proof, sixth attempt, 2026-10-08, sandbox agent-bureau-demo. All
# four slots were held. Epic A (DRE-6300) waited at place 1 of 1 from
# 14:08:49Z and epic B (DRE-6301) at place 2 of 2 from 14:09:08Z. At 14:09:26Z
# DRE-6287's run released its slot `because finished`, and in the same second
# single card C (DRE-6302), just moved to Planning, posted its `claimed`. C's
# claim step answered `admitted=true`, `place=0`, `waiting=2`; DRE-6287's
# `next` found nobody (`card=` at 14:09:27.96Z), and A waited until another
# release dispatched it at 14:12:04Z.

OCT8_A = "DRE-6300"
OCT8_B = "DRE-6301"
OCT8_C = "DRE-6302"
OCT8_RELEASER = "DRE-6287"
OCT8_RELEASER_RUN = "37789691121"
OCT8_C_RUN = "37790062876"
OCT8_DEMO = "dreadnought-foundry/agent-bureau-demo"
OCT8_FLEET = "dreadnought-foundry/agent-bureau"


def oct8(hms: str) -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    return datetime(2026, 10, 8, h, m, s, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 26. a claim never passes a card that was already waiting                     #
# --------------------------------------------------------------------------- #


class ArrivalWaitsBehindTheLine(_Base):
    def _receipt(self, ident: str, state: str, hms: str, run: str, *,
                 repo: str = OCT8_DEMO, trigger: str = "planning", **extra) -> None:
        at = oct8(hms)
        self.board.post(ident, pq.format_receipt(
            state, card=ident, run=run, repo=repo, trigger=trigger, at=iso(at),
            **extra), at=at)

    def _oct8(self) -> list:
        """The receipts on the board at 14:09:26Z, C's claim the newest."""
        keys = []
        for n, hms in enumerate(("13:51:02", "13:58:40", "14:04:15"), start=1):
            ident = self.board.add(f"DRE-K{n}")
            self._receipt(ident, "claimed", hms, f"run-K{n}")
            keys.append(ident)
        self.board.add(OCT8_RELEASER)
        self._receipt(OCT8_RELEASER, "claimed", "14:07:21", OCT8_RELEASER_RUN,
                      repo=OCT8_FLEET)
        self.board.add(OCT8_A)
        self._receipt(OCT8_A, "waiting", "14:08:49", "run-A", place=1, of=1)
        self.board.add(OCT8_B)
        self._receipt(OCT8_B, "waiting", "14:09:08", "run-B", place=2, of=2)
        self._receipt(OCT8_RELEASER, "released", "14:09:26", OCT8_RELEASER_RUN,
                      repo=OCT8_FLEET, because="finished")
        self.board.add(OCT8_C)
        self._receipt(OCT8_C, "claimed", "14:09:26", OCT8_C_RUN)
        self.board.clock = oct8("14:09:27")
        return keys

    def _settle_c(self, order=()) -> dict:
        with mock.patch.object(pq, "planning_order", lambda: list(order)):
            return pq.settle_claim(linear_ops, OCT8_C, run_id=OCT8_C_RUN,
                                   repo=OCT8_DEMO, trigger_state="planning")

    def _next(self, order=()):
        with mock.patch.object(pq, "planning_order", lambda: list(order)):
            return pq.next_in_line(pq.ordered_ledger(list(self.board.cards.values()),
                                                     now=self.board.clock))

    def test_the_fixture_has_a_slot_free_when_c_reads(self):
        """The guard against a vacuous replay: three planners running, one slot
        free, A and B in line — exactly what C's read saw."""
        self._oct8()
        led = self.board.ledger()
        self.assertEqual(len(led.running), 4)  # K1..K3 and C's own claim
        self.assertEqual(pq.taken_before(led, led._views[OCT8_C].open_claims()[0]), 3)
        self.assertEqual([w.card for w in led.waiting], [OCT8_A, OCT8_B])

    def test_the_replay_c_waits_at_place_3_of_3(self):
        self._oct8()
        out = self._settle_c()
        self.assertEqual((out["admitted"], out["place"], out["waiting"]),
                         ("false", "3", "3"))
        newest = self.board.newest(OCT8_C)
        self.assertEqual((newest.state, newest.run, newest.place, newest.of),
                         ("waiting", OCT8_C_RUN, 3, 3))
        self.assertIn("waiting for a planner: place 3 of 3",
                      self.board.nodes(OCT8_C)[0]["body"])

    def test_the_release_still_hands_the_slot_to_a(self):
        self._oct8()
        self._settle_c()
        led = self.board.ledger()
        self.assertEqual([w.card for w in led.waiting], [OCT8_A, OCT8_B, OCT8_C])
        self.assertEqual(led.free_slots(4), 1)
        self.assertEqual(self._next().card, OCT8_A)

    def test_the_ceos_order_is_kept(self):
        self._oct8()
        out = self._settle_c(order=[OCT8_B])
        self.assertEqual((out["admitted"], out["place"], out["waiting"]),
                         ("false", "3", "3"))
        self.assertEqual(self.board.newest(OCT8_C).state, "waiting")
        self.assertEqual(self._next(order=[OCT8_B]).card, OCT8_B)

    def test_an_approval_waits_at_the_front_and_is_served_next(self):
        self._oct8()
        self._settle_c()
        epic = self.board.add("DRE-EPIC", lane="In Progress")
        self._receipt(epic, "claimed", "14:09:28", "run-epic", trigger="in progress")
        self.board.clock = oct8("14:09:29")
        # One slot is free when the approval reads: three running, three waiting.
        self.assertEqual(self.board.ledger().free_slots(4), 0)  # its own claim is 4th
        self.assertEqual(pq.taken_before(self.board.ledger(),
                                         self.board.newest(epic)), 3)
        with mock.patch.object(pq, "planning_order", lambda: []):
            out = pq.settle_claim(linear_ops, epic, run_id="run-epic", repo=OCT8_DEMO,
                                  trigger_state="in progress", reason="approved")
        self.assertEqual((out["admitted"], out["place"], out["waiting"]),
                         ("false", "1", "4"))
        newest = self.board.newest(epic)
        self.assertEqual((newest.state, newest.place, newest.trigger, newest.reason),
                         ("waiting", 1, "in progress", "approved"))
        nxt = self._next()
        self.assertEqual((nxt.card, nxt.trigger, nxt.reason),
                         (epic, "in progress", "approved"))

    # --- the paths that keep admitting ------------------------------------ #

    def test_an_empty_line_with_a_free_slot_admits(self):
        fill_running(self.board, 3)
        card = self.board.add("DRE-ALONE")
        claim_write(self.board, card, "run-alone")
        out = claim_read(self.board, card, "run-alone")
        self.assertEqual((out["admitted"], out["waiting"]), ("true", "0"))
        self.assertFalse(any(r.state == "waiting" for r in self.board.receipts(card)))

    def test_the_card_the_release_dispatched_is_admitted_while_others_wait(self):
        self._oct8()
        self._settle_c()
        # DRE-6287's run: `next` names A, and its dispatch lands.
        self._receipt(OCT8_A, "dispatched", "14:09:28", OCT8_RELEASER_RUN,
                      repo=OCT8_DEMO)
        self._receipt(OCT8_A, "claimed", "14:09:50", "run-A-planner")
        self.board.clock = oct8("14:09:51")
        out = pq.settle_claim(linear_ops, OCT8_A, run_id="run-A-planner",
                              repo=OCT8_DEMO, trigger_state="planning")
        self.assertEqual(out["admitted"], "true")
        led = self.board.ledger()
        self.assertEqual([w.card for w in led.waiting], [OCT8_B, OCT8_C])
        self.assertEqual(len(led.running), 4)

    def test_a_handover_is_admitted_with_no_ledger_read_while_others_wait(self):
        keys = self._oct8()
        reads = self.board.board_reads
        out = pq.claim(linear_ops, keys[0], run_id="run-K1-review", repo=OCT8_DEMO,
                       trigger_state="planning", sent_by_run="run-K1",
                       reason="re-review")
        self.assertEqual((out["admitted"], out["inherited"]), ("true", "true"))
        self.assertEqual(self.board.board_reads, reads)

    def test_a_ledger_read_that_raises_still_admits_with_a_warning(self):
        self._oct8()
        card = self.board.add("DRE-BLIND")

        def boom(*a, **k):
            raise RuntimeError("Linear 503")

        stdout = io.StringIO()
        with mock.patch.object(linear_ops, "gql_paged", boom), \
                contextlib.redirect_stdout(stdout):
            out = pq.claim(linear_ops, card, run_id="run-blind", repo=OCT8_DEMO,
                           trigger_state="planning")
        self.assertEqual(out["admitted"], "true")
        self.assertIn("::warning::planner slot for DRE-BLIND: admitted without a "
                      "ledger read", stdout.getvalue())

    def test_a_card_that_joins_the_line_after_the_claim_does_not_hold_it_back(self):
        """Claim order still decides between two claims in flight: a card that
        posted its `waiting` AFTER this run's claim was not waiting when this
        run arrived, so this run passes nobody by taking the slot."""
        fill_running(self.board, 3)
        early, late = self.board.add("DRE-EARLY"), self.board.add("DRE-LATE")
        claim_write(self.board, early, "run-early")
        claim_write(self.board, late, "run-late")
        # The later claimant reads first: one slot, the earlier claim takes it.
        self.assertEqual(claim_read(self.board, late, "run-late")["admitted"], "false")
        self.assertEqual(claim_read(self.board, early, "run-early")["admitted"], "true")

    def test_the_docstring_says_a_claim_never_passes_a_waiting_card(self):
        self.assertIn("a claim never passes a card that is waiting",
                      " ".join(pq.__doc__.split()).lower())


if __name__ == "__main__":
    unittest.main()
